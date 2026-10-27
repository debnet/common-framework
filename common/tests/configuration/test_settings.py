from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings as django_settings
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from common.utils import patch_settings
from common.views import get_safe_settings, scripts


class SettingsTests(SimpleTestCase):
    def test_patch_settings_restores_values_and_missing_attributes_on_error(self):
        with override_settings(AUDIT_EXISTING=None):
            self.assertFalse(hasattr(django_settings, "AUDIT_MISSING"))
            with self.assertRaises(RuntimeError):
                with patch_settings(AUDIT_EXISTING="temporary", AUDIT_MISSING=True):
                    raise RuntimeError("test")
            self.assertIsNone(django_settings.AUDIT_EXISTING)
            self.assertFalse(hasattr(django_settings, "AUDIT_MISSING"))

    def test_nested_patch_settings(self):
        with override_settings(AUDIT_EXISTING="initial"):
            with patch_settings(AUDIT_EXISTING="outer"):
                with patch_settings(AUDIT_EXISTING="inner"):
                    self.assertEqual(django_settings.AUDIT_EXISTING, "inner")
                self.assertEqual(django_settings.AUDIT_EXISTING, "outer")
            self.assertEqual(django_settings.AUDIT_EXISTING, "initial")

    @override_settings(
        SECRET_KEY="private", INTERNAL_CONFIG={"credentials": ["private"]}
    )
    def test_public_settings_are_allowlisted(self):
        public = get_safe_settings()
        self.assertIn("STATIC_URL", public)
        self.assertIn("LANGUAGE_CODE", public)
        for key in ("SECRET_KEY", "INTERNAL_CONFIG", "DATABASES", "INSTALLED_APPS"):
            self.assertNotIn(key, public)

    @override_settings(
        PUBLIC_SETTINGS=("STATIC_URL", "PUBLIC_FEATURE"),
        PUBLIC_FEATURE={"enabled": True},
    )
    def test_public_settings_can_be_configured(self):
        self.assertEqual(
            get_safe_settings(),
            {
                "STATIC_URL": django_settings.STATIC_URL,
                "PUBLIC_FEATURE": {"enabled": True},
            },
        )

    @override_settings(PUBLIC_SETTINGS=())
    def test_public_settings_can_be_disabled(self):
        self.assertEqual(get_safe_settings(), {})

    @override_settings(SECRET_KEY="never-publish-this", INTERNAL_CONFIG="also-private")
    def test_rendered_scripts_do_not_expose_private_settings(self):
        request = APIRequestFactory().get("/common/scripts.js")
        with (
            patch("common.views.get_urls", return_value=SimpleNamespace(data={})),
            patch("common.views.user_infos", return_value=SimpleNamespace(data={})),
        ):
            response = scripts(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"never-publish-this", response.content)
        self.assertNotIn(b"also-private", response.content)
        self.assertIn(b"STATIC_URL", response.content)
