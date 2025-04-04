"""
Django settings for cybernexvpn project.

Generated by 'django-admin startproject' using Django 4.2.16.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""

import environ
import dj_database_url
from pathlib import Path

from yookassa import Configuration

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env.str("DJANGO_SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=True)

BASE_URL = env.str("BASE_URL", default="http://localhost:8000")

ALLOWED_HOSTS = [
    "cybernexvpn.ru",
    "77.238.236.90",

    # yookassa
    "185.71.76.0",
    "185.71.77.0",
    "77.75.153.0/25",
    "77.75.156.11",
    "77.75.156.35",
    "77.75.154.128",
    "2a02:5180::/32"
]

CSRF_TRUSTED_ORIGINS = [BASE_URL, ]

APPEND_SLASH = False

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "django_celery_beat",

    "nexvpn",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# DRF
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# Swagger
SPECTACULAR_SETTINGS = {
    'TITLE': 'NEX API Documentation',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': True,
    'CAMELIZE_NAMES': True,
    'COMPONENT_SPLIT_REQUEST': True,
    'ENABLE_FILE_UPLOADS': True,

    "AUTHENTICATION_WHITELIST": [],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-KEY",
            }
        }
    },
    "SECURITY": [{"ApiKeyAuth": []}],
}

ROOT_URLCONF = "cybernexvpn.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cybernexvpn.wsgi.application"

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
DATABASES = {"default": dj_database_url.config(default=env.str("DATABASE_URL"))}

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators
AUTH_USER_MODEL = "auth.User"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# CELERY
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", 'redis://redis:6379/1')

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = env.str("STATIC_TARGET_PATH", "./data/backend/static/")

BASE_URL = env.str("BASE_URL", "http://localhost:8000")

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

YOOKASSA_OAUTH_TOKEN = env.str("YOOKASSA_OAUTH_TOKEN")
Configuration.configure_auth_token(YOOKASSA_OAUTH_TOKEN)

ADMIN_API_KEY = env.str("ADMIN_API_KEY")

TG_BOT_URL = env.str("TG_BOT_URL")
TG_BOT_API_URL = env.str("TG_BOT_API_URL")
TG_BOT_API_KEY = env.str("TG_BOT_API_KEY")

# Business Variables
START_BALANCE = env.int("START_PRICE", 100)
DEFAULT_SUBSCRIPTION_PRICE = env.int("DEFAULT_SUBSCRIPTION_PRICE", 100)
INVITATION_BONUS = env.int("INVITATION_BONUS", 50)
