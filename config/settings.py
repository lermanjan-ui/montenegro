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
    "corsheaders",

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    'foodcost',
]

# 🔧 middleware
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    
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

# 💳 Платёжные шлюзы (значения — в переменных окружения Render). Пусто = выкл.
PAYME_MERCHANT_ID = os.environ.get("PAYME_MERCHANT_ID", "")
PAYME_SECRET_KEY = os.environ.get("PAYME_SECRET_KEY", "")
PAYME_CHECKOUT_URL = os.environ.get("PAYME_CHECKOUT_URL", "https://checkout.paycom.uz")
PAYME_ACCOUNT_FIELD = os.environ.get("PAYME_ACCOUNT_FIELD", "order_id")
PAYME_SUCCESS_URL = os.environ.get("PAYME_SUCCESS_URL", "")

CLICK_SERVICE_ID = os.environ.get("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID = os.environ.get("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY = os.environ.get("CLICK_SECRET_KEY", "")
CLICK_MERCHANT_USER_ID = os.environ.get("CLICK_MERCHANT_USER_ID", "")
CLICK_SUCCESS_URL = os.environ.get("CLICK_SUCCESS_URL", "")

# 📲 Eskiz (SMS для входа по коду). Значения — в переменных Render.
ESKIZ_EMAIL = os.environ.get("ESKIZ_EMAIL", "")
ESKIZ_PASSWORD = os.environ.get("ESKIZ_PASSWORD", "")
ESKIZ_BASE_URL = os.environ.get("ESKIZ_BASE_URL", "https://notify.eskiz.uz/api")
ESKIZ_FROM = os.environ.get("ESKIZ_FROM", "4546")  # 4546 — тестовый отправитель Eskiz
# Текст SMS с кодом. ДОЛЖЕН совпадать с одобренным шаблоном Eskiz. {code} — подстановка.
ESKIZ_OTP_TEMPLATE = os.environ.get(
    "ESKIZ_OTP_TEMPLATE",
    "Код верификации для входа в мобильное приложение Raccoon: {code}",
)

# 🔢 OTP и токены приложения
OTP_CODE_TTL_SECONDS = int(os.environ.get("OTP_CODE_TTL_SECONDS", "300"))
# Длина кода = числу нулей в одобренном шаблоне Eskiz («для входа» = 4).
OTP_CODE_LENGTH = int(os.environ.get("OTP_CODE_LENGTH", "4"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.environ.get("OTP_RESEND_COOLDOWN_SECONDS", "60"))
OTP_MAX_PER_HOUR = int(os.environ.get("OTP_MAX_PER_HOUR", "5"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))
APP_ACCESS_TOKEN_TTL_DAYS = int(os.environ.get("APP_ACCESS_TOKEN_TTL_DAYS", "30"))
APP_REFRESH_TOKEN_TTL_DAYS = int(os.environ.get("APP_REFRESH_TOKEN_TTL_DAYS", "180"))
# ВНИМАНИЕ: только для теста на стейдже (вернёт код в ответе). НЕ включать на проде!
OTP_EXPOSE_CODE_FOR_TESTING = os.environ.get("OTP_EXPOSE_CODE_FOR_TESTING", "") == "1"

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://raccoon.uz",
    "https://www.raccoon.uz",
]

CORS_ALLOW_METHODS = [
    "GET",
    "POST",
    "OPTIONS",
]

CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]

CORS_URLS_REGEX = r"^/api/.*$"