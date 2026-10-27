"""Les requêtes PostgreSQL sont vérifiées sans prétendre les exécuter sur SQLite."""

from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from common.operations import CreateFunctionUnaccent, CreateIndexUnaccent


class UnaccentOperationTests(SimpleTestCase):
    def editor(self, vendor="postgresql", extensions=((1,),)):
        cursor = Mock()
        cursor.fetchall.return_value = list(extensions)
        editor = Mock()
        editor.connection.vendor = vendor
        editor.connection.cursor.return_value = cursor
        editor._create_index_name.return_value = "example_name_unaccent"
        return editor

    def state(self):
        state = Mock()
        state.apps.get_model.return_value = SimpleNamespace(
            _meta=SimpleNamespace(db_table="example")
        )
        return state

    def test_function_creation_and_removal(self):
        operation = CreateFunctionUnaccent()
        editor = self.editor()
        self.assertIsNone(operation.state_forwards("common", None))
        operation.database_forwards("common", editor, None, None)
        self.assertIn(
            "CREATE OR REPLACE FUNCTION F_UNACCENT", editor.execute.call_args.args[0]
        )
        operation.database_backwards("common", editor, None, None)
        editor.execute.assert_called_with("DROP FUNCTION IF EXISTS F_UNACCENT(text);")

    def test_non_postgresql_is_rejected(self):
        for operation in (
            CreateFunctionUnaccent(),
            CreateIndexUnaccent("Example", [["name"]]),
        ):
            editor = self.editor(vendor="sqlite")
            with self.assertLogs("common.operations", level="ERROR"):
                operation.database_forwards("common", editor, None, None)
                operation.database_backwards("common", editor, None, None)
            editor.execute.assert_not_called()

    def test_missing_extension_prevents_creation(self):
        for operation in (
            CreateFunctionUnaccent(),
            CreateIndexUnaccent("Example", [["name"]]),
        ):
            editor = self.editor(extensions=((0,),))
            with self.assertLogs("common.operations", level="ERROR"):
                operation.database_forwards("common", editor, None, None)
            editor.execute.assert_not_called()

    def test_indexes_create_and_remove_btree_and_gin(self):
        operation = CreateIndexUnaccent("Example", [["name"]])
        editor = self.editor()
        state = self.state()
        self.assertIsNone(operation.state_forwards("common", state))
        operation.database_forwards("common", editor, state, state)
        sql = [call.args[0] for call in editor.execute.call_args_list]
        self.assertTrue(any("USING btree" in query for query in sql))
        self.assertTrue(any("USING gin" in query for query in sql))
        operation.database_backwards("common", editor, state, state)
        self.assertEqual(editor.execute.call_count, 4)

    def test_missing_trigram_extension_keeps_only_btree(self):
        editor = self.editor()
        editor.connection.cursor.return_value.fetchall.side_effect = [
            [(1,)],
            [(1,)],
            [(0,)],
        ]
        with self.assertLogs("common.operations", level="WARNING"):
            CreateIndexUnaccent("Example", [["name"]]).database_forwards(
                "common", editor, None, self.state()
            )
        editor.execute.assert_called_once()
        self.assertIn("USING btree", editor.execute.call_args.args[0])
