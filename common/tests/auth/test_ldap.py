"""Contrats LDAP simulés : aucun annuaire ni secret externe n'est utilisé."""

from types import ModuleType
from unittest.mock import MagicMock, Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from common.auth import LdapAuthenticationBackend


@override_settings(
    LDAP_ENABLE=True,
    LDAP_HOST="ldap.example.com",
    LDAP_LOGIN="{username}@example.com",
    LDAP_BASE="dc=example,dc=com",
    LDAP_FILTER="(uid={username})",
    LDAP_GROUP_PREFIX="ldap-",
    LDAP_ATTRIBUTES=["givenName", "sn", "mail", "memberOf"],
)
class LdapBackendTests(TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.search.return_value = True
        self.connection.response = [
            {
                "attributes": {
                    "givenName": "Alice",
                    "sn": "Example",
                    "mail": "alice@example.com",
                    "memberOf": ["CN=Readers,OU=Groups,DC=example,DC=com"],
                }
            }
        ]
        self.ldap = ModuleType("ldap3")
        self.ldap.__path__ = []
        self.ldap.Server = Mock()
        self.ldap.Connection = Mock(return_value=self.connection)
        utilities = ModuleType("ldap3.utils")
        utilities.__path__ = []
        conversion = ModuleType("ldap3.utils.conv")
        escapes = {
            ord("\\"): r"\5c",
            ord("*"): r"\2a",
            ord("("): r"\28",
            ord(")"): r"\29",
            ord("\x00"): r"\00",
        }
        self.escape_filter_chars = conversion.escape_filter_chars = Mock(
            side_effect=lambda value: value.translate(escapes)
        )
        module = patch.dict(
            "sys.modules",
            {
                "ldap3": self.ldap,
                "ldap3.utils": utilities,
                "ldap3.utils.conv": conversion,
            },
        )
        module.start()
        self.addCleanup(module.stop)
        self.backend = LdapAuthenticationBackend()

    def test_success_creates_account_and_groups(self):
        user = self.backend.authenticate(username="Alice", password="test-secret")
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "alice")
        self.assertEqual(user.email, "alice@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(
            list(user.groups.values_list("name", flat=True)), ["ldap-Readers"]
        )

    @override_settings(LDAP_ADMIN_GROUPS=["Readers"])
    def test_admin_group_grants_staff_and_superuser(self):
        user = self.backend.authenticate(username="Alice", password="test-secret")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    @override_settings(LDAP_STAFF_USERS=["alice"])
    def test_staff_allowlist_does_not_grant_superuser(self):
        user = self.backend.authenticate(username="Alice", password="test-secret")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_missing_user_returns_none(self):
        self.connection.search.return_value = False
        with self.assertLogs("common.auth", level="INFO"):
            self.assertIsNone(
                self.backend.authenticate(username="Alice", password="test-secret")
            )
        self.assertFalse(get_user_model().objects.exists())

    def test_connection_failure_returns_none(self):
        self.ldap.Connection.side_effect = RuntimeError("directory unavailable")
        with self.assertLogs("common.auth", level="WARNING"):
            self.assertIsNone(
                self.backend.authenticate(username="Alice", password="test-secret")
            )

    def test_empty_password_does_not_connect(self):
        self.assertIsNone(self.backend.authenticate(username="Alice", password=""))
        self.ldap.Server.assert_not_called()

    @override_settings(LDAP_ENABLE=False)
    def test_disabled_backend_does_not_connect(self):
        self.assertIsNone(
            self.backend.authenticate(username="Alice", password="test-secret")
        )
        self.ldap.Server.assert_not_called()

    def test_local_groups_are_preserved(self):
        user = get_user_model().objects.create_user("alice")
        local = Group.objects.create(name="local")
        stale = Group.objects.create(name="ldap-Old")
        user.groups.add(local, stale)
        user = self.backend.authenticate(username="alice", password="test-secret")
        self.assertEqual(
            set(user.groups.values_list("name", flat=True)), {"local", "ldap-Readers"}
        )

    def test_username_is_escaped_in_search_filter(self):
        """Régression AUDIT-06 : délègue l'échappement au helper ldap3."""
        username = "a*()\\\x00b"
        self.connection.search.return_value = False
        with self.assertLogs("common.auth", level="INFO"):
            self.assertIsNone(
                self.backend.authenticate(username=username, password="test-secret")
            )
        self.escape_filter_chars.assert_called_once_with(username)
        self.assertEqual(
            self.connection.search.call_args.args[1], r"(uid=a\2a\28\29\5c\00b)"
        )
        # Le filtre et l'identifiant de bind ont des règles distinctes.
        self.assertEqual(
            self.ldap.Connection.call_args.kwargs["user"], username + "@example.com"
        )
