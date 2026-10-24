"""AWS settings layered on the team's development settings."""
import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403


def required_env(name):
    value = os.environ.get(name, "")
    if not value.strip():
        raise ImproperlyConfigured(f"Set {name} before using production settings.")
    return value


DEBUG = False
SECRET_KEY = required_env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = [host.strip() for host in required_env("DJANGO_ALLOWED_HOSTS").split(",") if host.strip()]
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list explicit hosts.")

MIDDLEWARE = ["config.middleware.HealthCheckMiddleware", *MIDDLEWARE]  # noqa: F405
MIDDLEWARE.insert(MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
                  "whitenoise.middleware.WhiteNoiseMiddleware")
STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()]

# The public landing page can run before the account backend and database are ready.
# Restricted mode still excludes account and admin routes.
DEPLOYMENT_CHECK_ONLY = os.environ.get("DJANGO_DEPLOYMENT_CHECK_ONLY", "False").lower() in {"true", "1"}
if DEPLOYMENT_CHECK_ONLY:
    ROOT_URLCONF = "config.deployment_urls"
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": required_env("DJANGO_DATABASE_PATH"),
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": required_env("DB_HOST"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": required_env("DB_NAME"),
        "USER": required_env("DB_USER"),
        "PASSWORD": required_env("DB_PASSWORD"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"sslmode": "require", "connect_timeout": 5},
    }}
    # Instance disks are replaced on deploy, so uploads such as profile photos live
    # in a private S3 bucket. Pages link to short-lived presigned URLs.
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": required_env("DJANGO_MEDIA_BUCKET"),
            "region_name": required_env("DJANGO_MEDIA_REGION"),
            # The instance role can only read, write and delete keys under this prefix.
            "location": "media",
            "querystring_auth": True,
            "querystring_expire": 3600,
            "signature_version": "s3v4",
            "addressing_style": "virtual",
            # Checking for an existing name needs s3:ListBucket, which the role lacks.
            # Upload names are random UUIDs, so overwriting never replaces another file.
            "file_overwrite": True,
        },
    }

# The TLS proxy must replace this header instead of trusting client input.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
HTTPS_ENABLED = os.environ.get("DJANGO_HTTPS", "True").lower() in {"true", "1"}
if not HTTPS_ENABLED and not DEPLOYMENT_CHECK_ONLY:
    raise ImproperlyConfigured("Enable HTTPS before exposing application or account routes.")
SECURE_SSL_REDIRECT = HTTPS_ENABLED
SESSION_COOKIE_SECURE = HTTPS_ENABLED
CSRF_COOKIE_SECURE = HTTPS_ENABLED
SECURE_HSTS_SECONDS = 3600 if HTTPS_ENABLED else 0
