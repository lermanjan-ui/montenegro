"""Фискализация — минимальный шов под будущую интеграцию e-pos.

Сейчас фискализации «вживую» нет (нет доков/кредов API e-pos), поэтому работает
заглушка StubProvider. Весь остальной код кассы вызывает фискализацию только
через одну функцию — fiscalize_payment(). Когда появится реальный API, нужно:

  1. реализовать EposProvider.fiscalize() с настоящим HTTP-вызовом;
  2. вернуть его из get_provider() когда настройки «боевые».

Больше нигде ничего менять не придётся — точка подключения одна.
"""

from .models import PosPayment, FiscalizationSettings


class FiscalResult:
    """Результат фискализации одной оплаты."""

    def __init__(self, status, receipt_id="", error=""):
        self.status = status          # одно из PosPayment.FISCAL_*
        self.receipt_id = receipt_id  # номер фискального чека от провайдера
        self.error = error            # текст ошибки, если не удалось


class FiscalProvider:
    """Базовый интерфейс провайдера фискализации."""

    name = "base"

    def fiscalize(self, payment, settings):
        raise NotImplementedError


class StubProvider(FiscalProvider):
    """Заглушка: реального вызова нет. Статус — по режиму настроек.

    Боевой режим помечается как «фискализирован» без обращения к e-pos —
    ровно здесь позже встанет настоящий вызов (EposProvider)."""

    name = "stub"

    def fiscalize(self, payment, settings):
        mode = getattr(settings, "mode", None)
        if settings and mode == FiscalizationSettings.MODE_LIVE:
            # TODO(e-pos): здесь будет реальный вызов API и разбор ответа.
            return FiscalResult(PosPayment.FISCAL_DONE)
        if settings and mode == FiscalizationSettings.MODE_TEST:
            return FiscalResult(PosPayment.FISCAL_TEST)
        return FiscalResult(PosPayment.FISCAL_SKIPPED)


# ----- точка расширения -----------------------------------------------------
# Реестр провайдеров: когда появится реальный e-pos, добавить EposProvider и
# вернуть его из get_provider() при настройках боевого режима.
_PROVIDERS = {
    "stub": StubProvider,
}


def get_provider(country, settings):
    """Выбор провайдера по настройкам точки. Пока всегда заглушка."""
    return StubProvider()


def _settings_for(country):
    return FiscalizationSettings.objects.filter(
        tenant=country, location__isnull=True,
    ).first()


def fiscalize_payment(country, payment, settings=None):
    """ЕДИНСТВЕННАЯ точка фискализации оплаты. Вызывает провайдера и
    записывает результат в PosPayment. Возвращает FiscalResult."""
    if settings is None:
        settings = _settings_for(country)
    provider = get_provider(country, settings)
    result = provider.fiscalize(payment, settings)
    payment.fiscal_status = result.status
    payment.fiscal_receipt_id = result.receipt_id or ""
    payment.fiscal_error = result.error or ""
    payment.save(update_fields=["fiscal_status", "fiscal_receipt_id", "fiscal_error"])
    return result
