# -*- coding: utf-8 -*-
"""
Реквизиты компании-клиента (узбекские поля).

CompanyProfile — OneToOne к Country (арендатору). Все поля необязательные,
чтобы клиент мог заполнять постепенно. Валидация форматов (кол-во цифр и т.п.)
делается мягко на уровне формы/шаблона, а не блокирует сохранение черновика.
"""
from django.db import models

from .models import Country


class CompanyProfile(models.Model):
    country = models.OneToOneField(
        Country, on_delete=models.CASCADE, related_name="company_profile"
    )

    # ----- Основное -----
    legal_name = models.CharField(
        "Полное наименование", max_length=255, blank=True, default="",
        help_text='Напр.: ООО «Navruz Catering»')
    short_name = models.CharField(
        "Краткое наименование", max_length=255, blank=True, default="")
    ownership_form = models.CharField(
        "Форма собственности", max_length=100, blank=True, default="",
        help_text="ООО / ЧП / МЧЖ / АО и т.п.")

    # ----- Налоговые реквизиты -----
    inn = models.CharField(
        "СТИР (ИНН)", max_length=9, blank=True, default="",
        help_text="9 цифр")
    vat_payer = models.BooleanField("Плательщик НДС", default=False)
    vat_code = models.CharField(
        "Рег. код плательщика НДС", max_length=20, blank=True, default="",
        help_text="Если компания — плательщик НДС")
    oked = models.CharField(
        "ОКЭД", max_length=20, blank=True, default="",
        help_text="Код вида деятельности")
    okpo = models.CharField(
        "ОКПО", max_length=20, blank=True, default="")

    # ----- Банк -----
    bank_account = models.CharField(
        "Расчётный счёт (р/с)", max_length=20, blank=True, default="",
        help_text="20 цифр")
    bank_name = models.CharField(
        "Банк", max_length=255, blank=True, default="")
    mfo = models.CharField(
        "МФО банка", max_length=5, blank=True, default="",
        help_text="5 цифр")

    # ----- Адреса -----
    legal_address = models.TextField("Юридический адрес", blank=True, default="")
    actual_address = models.TextField("Фактический адрес", blank=True, default="")

    # ----- Руководство -----
    director_name = models.CharField(
        "ФИО директора", max_length=255, blank=True, default="")
    director_basis = models.CharField(
        "Действует на основании", max_length=100, blank=True, default="Устава")
    accountant_name = models.CharField(
        "ФИО главного бухгалтера", max_length=255, blank=True, default="")

    # ----- Контакты -----
    phone = models.CharField("Телефон", max_length=50, blank=True, default="")
    email = models.EmailField("E-mail", blank=True, default="")
    logo = models.ImageField(
        "Логотип", upload_to="company_logos/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Реквизиты · {self.country.name}"
