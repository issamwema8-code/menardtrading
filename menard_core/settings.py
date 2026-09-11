import os
import sys
from pathlib import Path
from decouple import config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
SECRET_KEY = config('SECRET_KEY', default='django-insecure-menard-logistics-key-2026-xyz8974123')
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'menardtrading.com',
    'www.menardtrading.com',
    '13.61.231.74',
    '*',
]

CSRF_TRUSTED_ORIGINS = [
    'https://menardtrading.com',
    'https://www.menardtrading.com',
    'http://menardtrading.com',
    'http://www.menardtrading.com',
    'https://13.61.231.74',
    'http://13.61.231.74',
    'http://13.61.231.74:8000',
    'http://13.61.231.74:8004',
    'http://localhost',
    'http://localhost:8000',
    'http://127.0.0.1',
    'http://127.0.0.1:8000',
    'http://127.0.0.1:8004',
]

# Allow dynamic custom origins from .env
_env_csrf_origins = config('CSRF_TRUSTED_ORIGINS', default='')
if _env_csrf_origins:
    CSRF_TRUSTED_ORIGINS += [origin.strip() for origin in _env_csrf_origins.split(',') if origin.strip()]

# Reverse proxy & HTTPS headers (Crucial for OpenLiteSpeed / Nginx / Gunicorn SSL termination)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Standard Cookie & CSRF Security Settings
CSRF_COOKIE_HTTPONLY = False  # Allows frontend JavaScript / HTMX to read csrftoken cookie safely
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=not DEBUG, cast=bool)
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=not DEBUG, cast=bool)

if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'



# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third-party apps
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',

    # Internal apps
    'apps.accounts',
    'apps.customers',
    'apps.orders',
    'apps.quotes',
    'apps.logistics',
    'apps.billing',
    'apps.accounting',
    'apps.webhooks',
    'apps.api',
]

MIDDLEWARE = [
    'menard_core.middleware.OpenLiteSpeedProxyMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'apps.accounts.middleware.TwoFactorEnforcementMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'menard_core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'menard_core.context_processors.branding_context',
                'apps.accounts.context_processors.rbac_context',
            ],
            'builtins': [
                'django.contrib.humanize.templatetags.humanize',
                'apps.billing.templatetags.menard_tags',
            ],
        },
    },
]

WSGI_APPLICATION = 'menard_core.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': config('DB_ENGINE', default='django.db.backends.mysql'),
        'NAME': config('DB_NAME', default='app_menardtrading'),
        'USER': config('DB_USER', default='app_menardtradinguser'),
        'PASSWORD': config('DB_PASSWORD', default='Menard@2026'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='3306'),
        'OPTIONS': {
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            'charset': 'utf8mb4',
        },
    }
}

# Use lightweight in-memory SQLite when running automated tests
if 'test' in sys.argv:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Authentication URLs & Redirects
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/overview/'
LOGOUT_REDIRECT_URL = '/login/'

# Fast password hasher for automated test execution
import sys
if 'test' in sys.argv:
    PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.MD5PasswordHasher',
    ]

# Internationalization & Number Formatting
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Johannesburg'
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (PDF uploads, PODs, Invoices, Receipts)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ============================================================
# DJANGO REST FRAMEWORK CONFIGURATION (API LAYER FOR MOBILE/WEB)
# ============================================================
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
}

CORS_ALLOW_ALL_ORIGINS = True

# ============================================================
# BREVO SMTP EMAIL CONFIGURATION
# ============================================================
BREVO_SMTP_HOST = config(
    'BREVO_SMTP_HOST',
    default='smtp-relay.brevo.com'
)

BREVO_SMTP_PORT = config(
    'BREVO_SMTP_PORT',
    cast=int,
    default=587
)

BREVO_SMTP_FALLBACK_PORT = config(
    'BREVO_SMTP_FALLBACK_PORT',
    cast=int,
    default=2525
)

BREVO_SMTP_USER = config(
    'BREVO_SMTP_USER',
    default=''
)

BREVO_SMTP_PASSWORD = config(
    'BREVO_SMTP_PASSWORD',
    default=''
)

# Departmental Mailbox Password
EMAIL_PASSWORD = config(
    'EMAIL_PASSWORD',
    default=''
)

# Common Brevo SMTP dictionary
BREVO_EMAIL_SETTINGS = {
    'EMAIL_HOST': BREVO_SMTP_HOST,
    'EMAIL_PORT': BREVO_SMTP_PORT,
    'EMAIL_USE_TLS': True,
    'EMAIL_USE_SSL': False,
    'EMAIL_HOST_USER': BREVO_SMTP_USER,
    'EMAIL_HOST_PASSWORD': BREVO_SMTP_PASSWORD,
}

# Departmental Sender Configurations
EMAIL_CONFIGS = {
    'orders': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading Orders <orders@menardtrading.com>',
    },
    'quotes': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading Quotations <quotes@menardtrading.com>',
    },
    'invoicing': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading Accounts <accounts@menardtrading.com>',
    },
    'operations': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading Logistics <logistics@menardtrading.com>',
    },
    'no-reply': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading No-Reply <no-reply@menardtrading.com>',
    },
    'support': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading Support <support@menardtrading.com>',
    },
    'info': {
        **BREVO_EMAIL_SETTINGS,
        'DEFAULT_FROM_EMAIL': 'Menard Trading Support <support@menardtrading.com>',
    },
}

# Production Email Settings (Default: support)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = EMAIL_CONFIGS['support']['EMAIL_HOST']
EMAIL_PORT = EMAIL_CONFIGS['support']['EMAIL_PORT']
EMAIL_USE_TLS = EMAIL_CONFIGS['support']['EMAIL_USE_TLS']
EMAIL_USE_SSL = EMAIL_CONFIGS['support']['EMAIL_USE_SSL']
EMAIL_HOST_USER = EMAIL_CONFIGS['support']['EMAIL_HOST_USER']
EMAIL_HOST_PASSWORD = EMAIL_CONFIGS['support']['EMAIL_HOST_PASSWORD']
DEFAULT_FROM_EMAIL = EMAIL_CONFIGS['support']['DEFAULT_FROM_EMAIL']
SERVER_EMAIL = EMAIL_CONFIGS['support']['EMAIL_HOST_USER']

BASE_URL = config('BASE_URL', default='https://menardtrading.com')

# Webhook Security
BREVO_WEBHOOK_SECRET = config('BREVO_WEBHOOK_SECRET', default='menard-secure-webhook-token-2026')

import base64
_emblem_png_path = BASE_DIR / 'static' / 'images' / 'menard_emblem.png'
_emblem_svg_path = BASE_DIR / 'static' / 'images' / 'menard_emblem.svg'
_emblem_b64 = ""
if _emblem_png_path.exists():
    try:
        with open(_emblem_png_path, 'rb') as _f:
            _emblem_b64 = f"data:image/png;base64,{base64.b64encode(_f.read()).decode('utf-8')}"
    except Exception:
        _emblem_b64 = ""
elif _emblem_svg_path.exists():
    try:
        with open(_emblem_svg_path, 'rb') as _f:
            _emblem_b64 = f"data:image/svg+xml;base64,{base64.b64encode(_f.read()).decode('utf-8')}"
    except Exception:
        _emblem_b64 = ""

# Centralized Brand Configuration (Default Initial Configuration for Menard Trading CC)
EMAIL_BRANDING = {
    'brand_name': 'MENARD TRADING CC',
    'company_name': 'MENARD TRADING CC',
    'tagline': 'ALWAYS ON TIME',
    'logo_url': f'{BASE_URL}/static/images/menard_emblem.png',
    'logo_png_url': f'{BASE_URL}/static/images/menard_emblem.png',
    'logo_svg': f'{BASE_URL}/static/images/menard_emblem.svg',
    'emblem_base64': _emblem_b64,
    'primary_color': '#0f172a',      # Slate Navy
    'secondary_color': '#1e293b',    # Dark Navy
    'accent_color': '#b47828',       # Menard Logo Gold / Ochre
    'accent_gold_light': '#dca048',  # Light Ochre
    'accent_gold_dark': '#8a5818',   # Deep Bronze
    'text_color': '#334155',
    'bg_color': '#f8fafc',
    'website_url': 'https://menardtrading.com',
    'support_email': 'support@menardtrading.com',
    'accounts_email': 'accounts@menardtrading.com',
    'orders_email': 'orders@menardtrading.com',
    'quotes_email': 'quotes@menardtrading.com',
    'phone': '+264 81 445 5188',
    'company_reg_number': 'CC/2022/03892',
    'vat_number': '13009715-11',
    'tax_number': '13009715-11',
    'postal_address': 'P O BOX 497-19001,',
    'physical_address': '',
    'city': 'RUNDU',
    'country': 'NAMIBIA',
    'company_address': 'P O BOX 497-19001, RUNDU - NAMIBIA',
    'bank_details': {
        'bank_name': '',
        'account_name': '',
        'account_number': '',
        'account_type': '',
        'branch_code': '',
        'branch_name': '',
        'swift_code': '',
    }
}

# ============================================================
# LOGGING CONFIGURATION (DIAGNOSTIC VISIBILITY FOR PROXY / CSRF)
# ============================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django.security.csrf': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}

