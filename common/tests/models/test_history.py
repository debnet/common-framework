"""History snapshots and commit boundaries, with and without an asynchronous publisher."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import expectedFailure, skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import isolate_apps

from common import models as common_models
from common.models import CommonModel, Entity, Global, History, HistoryField


def history_models():
    class HistoryTestTag(CommonModel):
        name = models.CharField(max_length=50)

        class Meta:
            app_label = "common"

    class HistoryTestEntity(Entity):
        # Actor attribution is tested on History.user; avoid a FK from temporary
        # tables to global auth tables so Django's ordinary test flush remains valid.
        current_user = None
        name = models.CharField(max_length=50)
        details = models.JSONField(default=dict)
        tags = models.ManyToManyField(HistoryTestTag, related_name="entities")

        class Meta:
            app_label = "common"

        def __str__(self):
            return self.name

    return HistoryTestTag, HistoryTestEntity


@override_settings(
    NOTIFY_CHANGES=False, IGNORE_GLOBAL=True, IGNORE_LOG=False, IGNORE_LOG_NO_USER=False
)
class HistoryTests(TransactionTestCase):
    databases = {"default", "other"} if "other" in connections else {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        registry = isolate_apps("common")
        registry.enable()
        cls.addClassCleanup(registry.disable)
        cls.tag_model, cls.entity_model = history_models()
        for alias in cls.databases:
            with connections[alias].schema_editor() as editor:
                editor.create_model(cls.tag_model)
                editor.create_model(cls.entity_model)

    @classmethod
    def tearDownClass(cls):
        try:
            for alias in cls.databases:
                with connections[alias].schema_editor() as editor:
                    editor.delete_model(cls.entity_model)
                    editor.delete_model(cls.tag_model)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        ContentType.objects.clear_cache()
        # No real broker or notification endpoint is used by the unit tests.
        synchronous = patch.object(common_models, "app", Mock(spec=[]))
        synchronous.start()
        self.addCleanup(synchronous.stop)
        for name in ("send_websocket", "send_http"):
            notification = patch.object(common_models.Webhook, name)
            notification.start()
            self.addCleanup(notification.stop)

    def _fixture_teardown(self):
        with override_settings(IGNORE_LOG=True, NOTIFY_CHANGES=False):
            for alias in self.databases:
                self.entity_model.objects.using(alias).all().delete(_force_default=True)
                self.tag_model.objects.using(alias).all().delete()
        super()._fixture_teardown()

    def entity(self, using="default"):
        # Temporary models do not have content types populated by migrations.
        ContentType.objects.db_manager(using).get_for_model(self.entity_model)
        entity = self.entity_model(name="Before", details={"value": "old"})
        entity.save(using=using, _ignore_log=True)
        return self.entity_model.objects.using(using).get(pk=entity.pk)

    def execute_published_fields(self, publisher):
        for call in publisher.call_args_list:
            # The actual broker contract contains only IDs, JSON strings and a DB alias.
            json.dumps(call.kwargs["kwargs"])
            common_models.log_history_fields(**call.kwargs["kwargs"])

    def test_synchronous_save_keeps_actor_context_and_fields(self):
        entity = self.entity()
        actor = get_user_model().objects.create(username="history-actor")
        entity.name = "After"
        with patch.object(common_models.log_history_fields, "apply_async") as publisher:
            entity.save(update_fields=["name"], _current_user=actor, _reason="Review")
        publisher.assert_not_called()
        history = entity._history
        self.assertEqual(history.user_id, actor.pk)
        self.assertEqual(history.reason, "Review")
        self.assertEqual(history.fields_count, 1)
        field = history.fields.get()
        self.assertEqual(
            (field.field_name, field.old_value, field.new_value),
            ("name", "Before", "After"),
        )

    @expectedFailure
    def test_replayed_history_fields_are_idempotent(self):
        """AUDIT-08 : rejouer les mêmes détails les insère une seconde fois."""
        entity = self.entity()
        entity.name = "After"
        entity.save(update_fields=["name"])
        history = entity._history
        payload = common_models.json_encode(
            list(
                history.fields.values(
                    "field_name",
                    "old_value",
                    "new_value",
                    "data",
                    "data_size",
                    "editable",
                    "status_m2m",
                )
            )
        )
        common_models.log_history_fields(
            history_id=history.pk, fields_json=payload, using="default"
        )
        history.refresh_from_db()
        self.assertEqual(history.fields_count, 1)
        self.assertEqual(history.fields.count(), 1)

    def test_create_and_restore_statuses(self):
        entity = self.entity_model.objects.create(name="Created")
        self.assertEqual(entity._history.status, History.CREATE)
        entity = self.entity_model.objects.get(pk=entity.pk)
        entity._restore = True
        entity.name = "Restored"
        entity.save(update_fields=["name"])
        self.assertEqual(entity._history.status, History.RESTORE)
        self.assertEqual(entity._history.fields.get().new_value, "Restored")

    def test_ignore_log_and_excluded_fields(self):
        entity = self.entity()
        entity.name = "Ignored"
        entity.save(update_fields=["name"], _ignore_log=True)
        self.assertFalse(History.objects.exists())
        entity = self.entity_model.objects.get(pk=entity.pk)
        entity.name = "Excluded"
        entity.save(update_fields=["name"], _ignore_log=["name"])
        self.assertFalse(History.objects.exists())

    def test_async_save_reserves_header_and_publishes_after_commit(self):
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            with transaction.atomic():
                entity.name = "After"
                entity.save(update_fields=["name"])
                self.assertTrue(History.objects.filter(pk=entity._history.pk).exists())
                self.assertFalse(HistoryField.objects.exists())
                publisher.assert_not_called()
                entity.name = "Not saved"
                entity._copy["name"] = "Changed after capture"
            publisher.assert_called_once()
            self.execute_published_fields(publisher)
        field = HistoryField.objects.get()
        self.assertEqual((field.old_value, field.new_value), ("Before", "After"))

    def test_json_values_are_detached_before_publication(self):
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            with transaction.atomic():
                entity.details = {"value": "new"}
                entity.save(update_fields=["details"])
                entity.details["value"] = "Later mutation"
                entity._copy["details"]["value"] = "Another mutation"
            self.execute_published_fields(publisher)
        field = HistoryField.objects.get()
        self.assertEqual(field.data, {"value": "old"})
        self.assertIn("new", field.new_value)
        self.assertNotIn("mutation", field.new_value)

    def test_rollback_discards_header_and_task_and_does_not_reuse_stale_header(self):
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            with self.assertRaises(RuntimeError), transaction.atomic():
                entity.name = "Rolled back"
                entity.save(update_fields=["name"])
                raise RuntimeError("rollback")
            publisher.assert_not_called()
            self.assertFalse(History.objects.exists())
            self.assertEqual(self.entity_model.objects.get(pk=entity.pk).name, "Before")
            entity.refresh_from_db()
            # Django does not restore arbitrary Python state on rollback/refresh.
            entity._copy = entity.to_dict(editables=True)
            entity.name = "Committed"
            entity.save(update_fields=["name"])
            self.execute_published_fields(publisher)
        self.assertEqual(History.objects.count(), 1)
        field = HistoryField.objects.get()
        self.assertEqual((field.old_value, field.new_value), ("Before", "Committed"))

    def test_multiple_saves_and_m2m_share_header_and_increment_counter(self):
        entity = self.entity()
        tag = self.tag_model.objects.create(name="Tag")
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            with transaction.atomic():
                entity.name = "First"
                entity.save(update_fields=["name"])
                history_id = entity._history.pk
                entity.name = "Second"
                entity.save(update_fields=["name"])
                entity.tags.add(tag)
                entity.tags.remove(tag)
                self.assertEqual(entity._history.pk, history_id)
            self.assertEqual(publisher.call_count, 4)
            self.execute_published_fields(publisher)
        history = History.objects.get()
        self.assertEqual(history.fields_count, 4)
        self.assertEqual(history.fields.count(), 4)
        changes = list(
            history.fields.filter(field_name="tags")
            .order_by("pk")
            .values_list("status_m2m", "old_value", "new_value")
        )
        self.assertEqual(
            changes,
            [
                (HistoryField.ADD_M2M, None, str(tag.pk)),
                (HistoryField.REMOVE_M2M, str(tag.pk), None),
            ],
        )

    def test_clear_snapshot_and_synchronous_m2m(self):
        entity = self.entity()
        tag = self.tag_model.objects.create(name="Tag")
        entity.tags.add(tag)
        entity.tags.clear()
        field = entity._history.fields.get(status_m2m=HistoryField.CLEAR_M2M)
        self.assertEqual(field.data, [tag.pk])
        self.assertEqual(field.old_value, str(tag.pk))
        self.assertIsNone(field.new_value)

    def test_delete_captures_relations_before_they_disappear(self):
        entity = self.entity()
        tag = self.tag_model.objects.create(name="Tag")
        entity._ignore_log = True
        entity.tags.add(tag)
        entity = self.entity_model.objects.get(pk=entity.pk)
        with patch.object(common_models, "app", Mock(spec=["send_task"])):
            entity.delete()
        history = History.objects.get(status=History.DELETE)
        self.assertEqual(history.data["tags_ids"], [tag.pk])
        self.assertFalse(self.entity_model.objects.exists())

    def test_pending_details_are_skipped_when_deletion_removes_their_header(self):
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            entity.name = "After"
            entity.save(update_fields=["name"])
            history_id = entity._history.pk
            entity.delete()
        self.assertFalse(History.objects.filter(pk=history_id).exists())
        deletion = History.objects.get(status=History.DELETE)
        self.assertEqual(deletion.data["name"], "After")
        publisher.assert_called_once()
        with self.assertLogs(common_models.logger, level="DEBUG") as logs:
            result = common_models.log_history_fields(
                **publisher.call_args.kwargs["kwargs"]
            )
        self.assertIsNone(result)
        self.assertEqual(
            logs.records[0].getMessage(),
            f"History fields skipped for missing history {history_id} (database default, 1 fields)",
        )
        self.assertFalse(HistoryField.objects.exists())
        self.assertEqual(History.objects.count(), 1)
        deletion.refresh_from_db()
        self.assertIsNone(deletion.fields_count)

    def test_synchronous_details_are_written_before_deletion(self):
        entity = self.entity()
        with self.assertLogs(common_models.logger, level="DEBUG") as logs:
            entity.name = "After"
            entity.save(update_fields=["name"])
            self.assertEqual(entity._history.fields.get().new_value, "After")
            entity.delete()
        self.assertFalse(
            any(
                "skipped for missing history" in record.getMessage()
                for record in logs.records
            )
        )
        self.assertEqual(History.objects.get().status, History.DELETE)

    def test_detail_failure_rolls_back_fields_and_counter_together(self):
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            entity.name = "After"
            entity.save(update_fields=["name"])
        payload = publisher.call_args.kwargs["kwargs"]
        with (
            patch.object(
                History, "save", side_effect=RuntimeError("counter write failed")
            ),
            self.assertRaises(RuntimeError),
        ):
            common_models.log_history_fields(**payload)
        self.assertFalse(HistoryField.objects.exists())
        self.assertIsNone(History.objects.get().fields_count)

    def test_concurrent_details_do_not_lose_counter_updates(self):
        if connections["default"].vendor != "postgresql":
            self.skipTest("Exercises PostgreSQL row locking")
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            entity.name = "First"
            entity.save(update_fields=["name"])
            entity.name = "Second"
            entity.save(update_fields=["name"])
        barrier = Barrier(2, timeout=10)

        def consume(payload):
            try:
                barrier.wait()
                common_models.log_history_fields(**payload)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [
                workers.submit(consume, call.kwargs["kwargs"])
                for call in publisher.call_args_list
            ]
            for future in futures:
                future.result(timeout=20)
        history = History.objects.get()
        self.assertEqual(history.fields.count(), 2)
        self.assertEqual(history.fields_count, 2)

    @skipUnless("other" in connections, "Requires a second disposable database")
    def test_history_uses_source_database_and_its_commit(self):
        entity = self.entity(using="other")
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async") as publisher,
        ):
            with transaction.atomic(using="other"):
                entity.name = "Other database"
                entity.save(using="other", update_fields=["name"])
                publisher.assert_not_called()
            self.execute_published_fields(publisher)
        self.assertFalse(History.objects.using("default").exists())
        self.assertEqual(History.objects.using("other").get().fields_count, 1)

    @override_settings(IGNORE_GLOBAL=False)
    def test_global_creation_is_preserved(self):
        entity = self.entity_model.objects.create(name="Global")
        self.assertEqual(Global.objects.get().object_uid, entity.uuid)

    @override_settings(NOTIFY_CHANGES=True, WEBSOCKET_ENABLED=True)
    def test_notifications_capture_m2m_and_wait_for_commit(self):
        entity = self.entity()
        tag = self.tag_model.objects.create(name="Tag")
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async"),
            patch.object(common_models.send_notify_changes, "apply_async") as publisher,
            transaction.atomic(),
        ):
            entity.tags.add(tag)
            entity.tags.remove(tag)
            publisher.assert_not_called()
        changes = [
            json.loads(call.kwargs["kwargs"]["data_json"])
            for call in publisher.call_args_list
            if call.kwargs["kwargs"]["status"] == History.M2M
        ]
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["changes"]["m2m"]["current"]["tags"], [tag.pk])
        self.assertEqual(changes[1]["changes"]["m2m"]["previous"]["tags"], [tag.pk])

    @override_settings(NOTIFY_CHANGES=True, WEBSOCKET_ENABLED=True)
    def test_rollback_discards_notifications(self):
        entity = self.entity()
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async"),
            patch.object(common_models.send_notify_changes, "apply_async") as publisher,
        ):
            with self.assertRaises(RuntimeError), transaction.atomic():
                entity.name = "Rolled back"
                entity.save(update_fields=["name"])
                raise RuntimeError("rollback")
            publisher.assert_not_called()

    @skipUnless("other" in connections, "Requires a second disposable database")
    @override_settings(NOTIFY_CHANGES=True, WEBSOCKET_ENABLED=False)
    def test_http_notifications_use_the_source_database(self):
        with override_settings(NOTIFY_CHANGES=False):
            entity = self.entity(using="other")
            webhook = common_models.Webhook.objects.using("other").create(
                url="https://example.invalid/hook"
            )
            content_type = ContentType.objects.db_manager("other").get_for_model(
                self.entity_model
            )
            webhook.types.add(content_type)
        with (
            patch.object(common_models, "app", Mock(spec=["send_task"])),
            patch.object(common_models.log_history_fields, "apply_async"),
            patch.object(common_models.send_notify_changes, "apply_async") as publisher,
        ):
            entity.name = "Changed in other"
            entity.save(using="other", update_fields=["name"])
        publisher.assert_called_once()
        payload = publisher.call_args.kwargs["kwargs"]
        self.assertEqual(payload["using"], "other")
        common_models.send_notify_changes(**payload)
        common_models.Webhook.send_http.assert_called_once()
        self.assertFalse(common_models.Webhook.objects.using("default").exists())

    @skipUnless(
        hasattr(common_models.log_history_fields, "app"),
        "Requires Celery enabled before Django setup",
    )
    def test_real_celery_worker_consumes_json_snapshots(self):
        from celery.contrib.testing.worker import start_worker

        celery_app = common_models.log_history_fields.app
        entity = self.entity()
        tag = self.tag_model.objects.create(name="Worker tag")

        @celery_app.task(name="common.tests.history_finished")
        def finished():
            # The memory-broker worker has its own Django connection in another thread.
            connections.close_all()
            return True

        with patch.object(common_models, "app", celery_app):
            with transaction.atomic():
                entity.name = "First worker change"
                entity.save(update_fields=["name"])
                entity.name = "Second worker change"
                entity.save(update_fields=["name"])
                entity.tags.add(tag)
                entity.tags.remove(tag)
                self.assertFalse(HistoryField.objects.exists())
            with start_worker(
                celery_app,
                pool="solo",
                concurrency=1,
                perform_ping_check=False,
                loglevel="ERROR",
            ):
                self.assertTrue(
                    finished.delay().get(timeout=20, disable_sync_subtasks=False)
                )
        history = History.objects.get()
        self.assertEqual(history.fields_count, 4)
        changes = list(
            history.fields.filter(field_name="name")
            .order_by("pk")
            .values_list("old_value", "new_value")
        )
        self.assertEqual(
            changes,
            [
                ("Before", "First worker change"),
                ("First worker change", "Second worker change"),
            ],
        )
        self.assertEqual(history.fields.filter(field_name="tags").count(), 2)
