from unittest.mock import patch

from django.test import SimpleTestCase

from common.router import DatabaseOverrideRouter, database_override, local_thread


class DatabaseRouterTests(SimpleTestCase):
    def setUp(self):
        for name in ("db_read_override", "db_write_override"):
            state = patch.object(local_thread, name, ["default"], create=True)
            state.start()
            self.addCleanup(state.stop)
        argv = patch("common.router.sys.argv", ["runner"])
        argv.start()
        self.addCleanup(argv.stop)
        self.router = DatabaseOverrideRouter()

    def test_nested_overrides_restore_previous_routing(self):
        with database_override("reader", read=True, write=False):
            self.assertEqual(self.router.db_for_read(None), "reader")
            self.assertEqual(self.router.db_for_write(None), "default")
            with database_override("writer", read=False, write=True):
                self.assertEqual(self.router.db_for_read(None), "reader")
                self.assertEqual(self.router.db_for_write(None), "writer")
            self.assertEqual(self.router.db_for_write(None), "default")
        self.assertEqual(self.router.db_for_read(None), "default")

    def test_exception_restores_routing(self):
        with self.assertRaises(ValueError):
            with database_override("other", write=True):
                raise ValueError("test")
        self.assertEqual(self.router.db_for_read(None), "default")
        self.assertEqual(self.router.db_for_write(None), "default")

    def test_decorator_routes_only_during_call(self):
        @database_override("other", write=True)
        def current():
            return self.router.db_for_read(None), self.router.db_for_write(None)

        self.assertEqual(current(), ("other", "other"))
        self.assertEqual(self.router.db_for_read(None), "default")

    def test_manage_py_test_explicitly_uses_default(self):
        with (
            database_override("other", write=True),
            patch("common.router.sys.argv", ["manage.py", "test"]),
        ):
            self.assertEqual(self.router.db_for_read(None), "default")
            self.assertEqual(self.router.db_for_write(None), "default")

    def test_router_policy(self):
        self.assertTrue(self.router.allow_relation(None, None))
        self.assertIsNone(self.router.allow_migrate(None))
        self.assertIsNone(self.router.allow_syncdb(None))

    def test_default_override_is_valid(self):
        """Régression AUDIT-03 : using=None sélectionne explicitement default."""
        with database_override(write=True):
            self.assertEqual(self.router.db_for_read(None), "default")
            self.assertEqual(self.router.db_for_write(None), "default")

    def test_default_override_restores_enclosing_routing(self):
        with database_override("other", write=True):
            with database_override():
                self.assertEqual(self.router.db_for_read(None), "default")
                self.assertEqual(self.router.db_for_write(None), "other")
            self.assertEqual(self.router.db_for_read(None), "other")
        self.assertEqual(self.router.db_for_read(None), "default")
        self.assertEqual(self.router.db_for_write(None), "default")
