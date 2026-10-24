import json
import os
import secrets
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings


@override_settings(MIDDLEWARE=["config.middleware.HealthCheckMiddleware", *settings.MIDDLEWARE])
class ProbeTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=["nyc-active.example"], SECURE_SSL_REDIRECT=True)
    def test_internal_liveness_probe_works_without_tls_or_database(self):
        response = self.client.get("/health/", HTTP_HOST="10.0.0.10:80")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["nyc-active.example"])
    def test_probe_host_exception_does_not_apply_to_other_pages(self):
        response = self.client.get("/", HTTP_HOST="untrusted.example")
        self.assertEqual(response.status_code, 400)

    def test_probe_rejects_state_changing_requests(self):
        self.assertEqual(self.client.post("/health/").status_code, 405)

    @patch("config.views.connection")
    def test_database_failure_reports_unavailable_without_leaking_details(self, database):
        database.cursor.side_effect = OperationalError("private DB detail")
        response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn(b"private DB detail", response.content)


class DatabaseProbeTests(TestCase):
    def test_readiness_checks_a_real_database_connection(self):
        with self.assertNumQueries(1):
            response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class LandingPageTests(SimpleTestCase):
    def test_frontend_landing_page_renders_without_database_access(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "landing.html")
        self.assertContains(response, "Find your")
        self.assertContains(response, "next game.")
        self.assertNotContains(response, "This temporary page will be replaced")


class ProductionSettingsTests(SimpleTestCase):
    def production_environment(self):
        return {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.production",
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_ALLOWED_HOSTS": "nyc-active.example",
            "DJANGO_HTTPS": "True",
            "DJANGO_DEPLOYMENT_CHECK_ONLY": "False",
            "DB_HOST": "database.example",
            "DB_NAME": "nyc_active",
            "DB_USER": "application_user",
            "DB_PASSWORD": "test-only-not-a-real-password",
            "DJANGO_MEDIA_BUCKET": "nyc-active-media-test",
            "DJANGO_MEDIA_REGION": "us-east-1",
        }

    def load_production(self, environment, code=None):
        code = code or (
            "from config.wsgi import application; "
            "from django.conf import settings; import json; "
            "print(json.dumps({'debug':settings.DEBUG, "
            "'ssl_redirect':settings.SECURE_SSL_REDIRECT, "
            "'secure_cookie':settings.SESSION_COOKIE_SECURE, "
            "'database_engine':settings.DATABASES['default']['ENGINE']}))"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_production_requires_a_secret(self):
        environment = self.production_environment()
        environment.pop("DJANGO_SECRET_KEY")
        result = self.load_production(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_SECRET_KEY", result.stderr)

    def test_production_does_not_fall_back_to_sqlite(self):
        environment = self.production_environment()
        environment.pop("DB_HOST")
        result = self.load_production(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DB_HOST", result.stderr)

    def test_production_requires_the_media_bucket_and_region(self):
        for name in ("DJANGO_MEDIA_BUCKET", "DJANGO_MEDIA_REGION"):
            with self.subTest(name=name):
                environment = self.production_environment()
                environment.pop(name)
                result = self.load_production(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Set {name}", result.stderr)

    def test_uploads_use_the_private_media_bucket_through_presigned_urls(self):
        environment = self.production_environment()
        # Placeholder credentials keep the check offline: signing a URL never calls AWS.
        environment.update(
            AWS_ACCESS_KEY_ID="test-only-access-key",
            AWS_SECRET_ACCESS_KEY="test-only-secret-key",
            AWS_CONFIG_FILE=os.devnull,
            AWS_SHARED_CREDENTIALS_FILE=os.devnull,
            AWS_EC2_METADATA_DISABLED="true",
        )
        for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
            environment.pop(name, None)
        code = (
            "from config.wsgi import application; "
            "from django.core.files.storage import storages; import json; "
            "storage=storages['default']; "
            "print(json.dumps({'backend':f'{type(storage).__module__}.{type(storage).__name__}', "
            "'bucket':storage.bucket_name, 'region':storage.region_name, "
            "'location':storage.location, 'overwrite':storage.file_overwrite, "
            "'url':storage.url('avatars/test.jpg')}))"
        )
        result = self.load_production(environment, code)
        self.assertEqual(result.returncode, 0, result.stderr)
        storage = json.loads(result.stdout)
        self.assertEqual(storage["backend"], "storages.backends.s3.S3Storage")
        self.assertEqual((storage["bucket"], storage["region"]), ("nyc-active-media-test", "us-east-1"))
        # Avoids the existence check, which would need s3:ListBucket.
        self.assertTrue(storage["overwrite"])

        url = urlsplit(storage["url"])
        query = parse_qs(url.query)
        self.assertEqual(url.scheme, "https")
        self.assertTrue(url.hostname.startswith("nyc-active-media-test.s3."), url.hostname)
        self.assertEqual(url.path, "/media/avatars/test.jpg")
        self.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
        self.assertEqual(query["X-Amz-Expires"], ["3600"])
        self.assertIn("X-Amz-Signature", query)

        # The instance role's grant must cover the prefix the storage writes to.
        policy = json.loads((settings.BASE_DIR / "infra/media-storage-policy.json").read_text())
        self.assertTrue(policy["Statement"][0]["Resource"].endswith(f"/{storage['location']}/*"))

    def test_production_rejects_unrestricted_hosts(self):
        environment = self.production_environment()
        environment["DJANGO_ALLOWED_HOSTS"] = "*"
        result = self.load_production(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit hosts", result.stderr)

    def test_wsgi_uses_explicit_production_settings(self):
        environment = self.production_environment()
        result = self.load_production(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "debug": False,
                "ssl_redirect": True,
                "secure_cookie": True,
                "database_engine": "django.db.backends.postgresql",
            },
        )

    def test_deployment_check_mode_requires_an_explicit_sqlite_path(self):
        environment = self.production_environment()
        environment["DJANGO_DEPLOYMENT_CHECK_ONLY"] = "True"
        environment.pop("DJANGO_DATABASE_PATH", None)
        result = self.load_production(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_DATABASE_PATH", result.stderr)

    def test_deployment_check_mode_exposes_no_admin_or_login_routes(self):
        environment = self.production_environment()
        environment.update(
            DJANGO_DEPLOYMENT_CHECK_ONLY="True",
            DJANGO_DATABASE_PATH=os.path.join(tempfile.gettempdir(), "nyc-active-check.sqlite3"),
            DJANGO_HTTPS="False",
        )
        for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD",
                     "DJANGO_MEDIA_BUCKET", "DJANGO_MEDIA_REGION"):
            environment.pop(name)
        collected = subprocess.run(
            [sys.executable, "manage.py", "collectstatic", "--noinput", "--verbosity", "0"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(collected.returncode, 0, collected.stderr)
        code = (
            "import re; "
            "from config.wsgi import application; "
            "from django.test import Client; "
            "client=Client(HTTP_HOST='nyc-active.example'); "
            "page=client.get('/'); "
            "assert page.status_code == 200; "
            "assert b'Find your' in page.content; "
            "assets=set(re.findall(r'(?:src|href)=\"(/static/[^\"]+)\"', page.content.decode())); "
            "assert len(assets) >= 8, assets; "
            "assert all(client.get(asset).status_code == 200 for asset in assets), assets; "
            "assert client.get('/health/').status_code == 200; "
            "assert client.get('/admin/').status_code == 404; "
            "assert client.get('/admin/login/').status_code == 404; "
            "assert client.get('/login/').status_code == 404; "
            "assert client.get('/register/').status_code == 404; "
            "assert client.get('/accounts/login/').status_code == 404"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_application_routes_require_https(self):
        environment = self.production_environment()
        environment["DJANGO_HTTPS"] = "False"
        result = self.load_production(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Enable HTTPS", result.stderr)


class LocalMediaStorageTests(SimpleTestCase):
    def test_development_settings_keep_uploads_on_local_disk(self):
        self.assertIsInstance(storages["default"], FileSystemStorage)
