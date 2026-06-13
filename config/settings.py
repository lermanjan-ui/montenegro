import os
from pathlib import Path
from dotenv import load_dotenv
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# 🔐 безопасность
SECRET_KEY = 'django-insecure-987789'

DEBUG = True

ALLOWED_HOSTS = ["*"]


# 🔧 приложения
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    'foodcost',  # твое приложение
]

# 🔧 middleware
MIDDLEWARE = [
    "config.cors_middleware.PublicCorsMiddleware",
    'django.middleware.security.SecurityMiddleware',

    # 👇 обязательно для Railway
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'  # ⚠️ если у тебя другое имя — скажи

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',

                'foodcost.context_processors.menu_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'  # ⚠️ если не config — скажи


# 🧠 база данных (Railway автоматически подставит PostgreSQL)
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


# 🌍 локализация
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True


# 📦 статические файлы
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'


# 🖼 медиа-файлы (загрузки админов: фото блюд, галерея, фото категорий)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# 🔚
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# =============================================================================
# 💳 CLICK PAYMENT INTEGRATION
# =============================================================================
# Credentials are read from environment variables ONLY — never hardcode the
# secret key in code or commit it to the repo. If CLICK_SECRET_KEY was ever
# exposed (logs, screenshots, frontend bundles, public commits), rotate it
# in the Click merchant cabinet BEFORE deploying to production.
#
# Required for Part 1 (URL build):
#   CLICK_SERVICE_ID       — Click service ID (numeric, public, used in URL)
#   CLICK_MERCHANT_ID      — Click merchant ID (numeric, public, used in URL)
#   CLICK_MERCHANT_USER_ID — used by Click API; not part of the checkout URL
#   CLICK_SUCCESS_URL      — where Click redirects after successful payment
#   CLICK_FAIL_URL         — where Click redirects after failed payment
#
# Required for Part 2 (callback verification — NOT in URLs):
#   CLICK_SECRET_KEY       — server-only; used to compute/verify md5 sign_string
#
# All values default to empty string so missing env doesn't crash boot; the
# build_click_payment_url() helper validates them at call time and surfaces
# a clear error if anything is missing.
CLICK_SERVICE_ID = os.environ.get("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID = os.environ.get("CLICK_MERCHANT_ID", "")
CLICK_MERCHANT_USER_ID = os.environ.get("CLICK_MERCHANT_USER_ID", "")
CLICK_SECRET_KEY = os.environ.get("CLICK_SECRET_KEY", "")
CLICK_SUCCESS_URL = os.environ.get(
    "CLICK_SUCCESS_URL",
    "https://raccoon-frontend.onrender.com/order-success",
)
CLICK_FAIL_URL = os.environ.get(
    "CLICK_FAIL_URL",
    "https://raccoon-frontend.onrender.com/checkout?payment_failed=1",
)


# =============================================================================
# 💳 PAYME (PAYCOM) PAYMENT INTEGRATION
# =============================================================================
# Payme is a separate provider from Click — uses JSON-RPC 2.0 Merchant API
# and Basic Auth (NOT a signature). Credentials are server-only.
#
# Required:
#   PAYME_MERCHANT_ID    — cashbox id (24-char hex from cabinet); public
#   PAYME_SECRET_KEY     — server-only; used in HTTP Basic on callback
#   PAYME_CHECKOUT_URL   — https://checkout.paycom.uz (live)
#                          or https://test.paycom.uz (sandbox)
#   PAYME_ACCOUNT_FIELD  — name of the account sub-field that holds the
#                          order id (configured in Payme cabinet). Default
#                          "order_id"; sent as ac.<PAYME_ACCOUNT_FIELD>.
#   PAYME_SUCCESS_URL    — where Payme redirects after a successful payment
#   PAYME_FAIL_URL       — where Payme redirects after a failed payment
PAYME_MERCHANT_ID = os.environ.get("PAYME_MERCHANT_ID", "")
PAYME_SECRET_KEY = os.environ.get("PAYME_SECRET_KEY", "")
PAYME_CHECKOUT_URL = os.environ.get(
    "PAYME_CHECKOUT_URL",
    "https://checkout.paycom.uz",
)
PAYME_ACCOUNT_FIELD = os.environ.get("PAYME_ACCOUNT_FIELD", "order_id")
PAYME_SUCCESS_URL = os.environ.get(
    "PAYME_SUCCESS_URL",
    "https://raccoon-frontend.onrender.com/order-success",
)
PAYME_FAIL_URL = os.environ.get(
    "PAYME_FAIL_URL",
    "https://raccoon-frontend.onrender.com/checkout?payment_failed=1",
)


# =============================================================================
# Online-payment TTL
# =============================================================================
# How long an online-payment order can sit in awaiting_payment before our
# cron / lazy-expire path declares it dead. Default 24h. Set via env if
# operations want a different window.
ORDER_AWAITING_PAYMENT_TTL_HOURS = int(
    os.environ.get("ORDER_AWAITING_PAYMENT_TTL_HOURS", "24")
)


# =============================================================================
# 📲 ESKIZ SMS — OTP для входа в приложение (/api/app/auth/*)
# =============================================================================
# Креды и параметры читаются ТОЛЬКО из окружения. Без ESKIZ_EMAIL/ESKIZ_PASSWORD
# клиент считается «не настроенным» и /api/app/auth/request-code отдаёт
# 503 SMS_NOT_CONFIGURED (как сейчас на проде). ESKIZ_OTP_TEMPLATE должен
# ДОСЛОВНО совпадать с одобренным в кабинете Eskiz шаблоном (подставляется {code}).
ESKIZ_EMAIL = os.environ.get("ESKIZ_EMAIL", "")
ESKIZ_PASSWORD = os.environ.get("ESKIZ_PASSWORD", "")
ESKIZ_FROM = os.environ.get("ESKIZ_FROM", "4546")
ESKIZ_OTP_TEMPLATE = os.environ.get("ESKIZ_OTP_TEMPLATE", "Kod: {code}")
ESKIZ_BASE_URL = os.environ.get("ESKIZ_BASE_URL", "https://notify.eskiz.uz/api")

# OTP: на dev/staging можно вернуть код прямо в ответе request-code (для отладки
# входа без живого SMS). На ПРОДЕ должно быть False, иначе код утечёт в ответ API.
OTP_EXPOSE_CODE_FOR_TESTING = (
    os.environ.get("OTP_EXPOSE_CODE_FOR_TESTING", "").strip().lower()
    in ("1", "true", "yes", "on")
)
OTP_CODE_TTL_SECONDS = int(os.environ.get("OTP_CODE_TTL_SECONDS", "300"))
OTP_CODE_LENGTH = int(os.environ.get("OTP_CODE_LENGTH", "4"))
