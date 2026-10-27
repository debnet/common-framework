# coding: utf-8
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import HttpResponse
from django.test import TransactionTestCase
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from common.api.decorators import atomic
from common.api.utils import api_view_with_serializer


class AtomicApiTests(TransactionTestCase):
    def create(self):
        return Group.objects.create(name="atomic-test")

    def assert_rolled_back(self):
        self.assertFalse(Group.objects.filter(name="atomic-test").exists())

    def test_success_commits_and_preserves_function_metadata(self):
        @atomic
        def view():
            """Test view."""
            self.create()
            return Response({"ok": True})

        response = view()
        self.assertEqual(response.data, {"ok": True})
        self.assertFalse(response.is_rendered)
        self.assertTrue(Group.objects.filter(name="atomic-test").exists())
        self.assertEqual(view.__name__, "view")
        self.assertEqual(view.__doc__, "Test view.")

    def test_error_responses_roll_back(self):
        for status in (400, 403, 500):
            with self.subTest(status=status):

                @atomic()
                def view():
                    self.create()
                    return Response({"error": "refused"}, status=status)

                self.assertEqual(view().status_code, status)
                self.assert_rolled_back()

    def test_plain_http_error_response_rolls_back(self):
        @atomic
        def view():
            self.create()
            return HttpResponse(status=409)

        self.assertEqual(view().status_code, 409)
        self.assert_rolled_back()

    def test_exceptions_are_not_hidden_or_reclassified(self):
        for error in (
            RuntimeError("server"),
            AssertionError("bug"),
            PermissionDenied(),
        ):
            with self.subTest(error=type(error).__name__):

                @atomic
                def view():
                    self.create()
                    raise error

                with self.assertRaises(type(error)) as caught:
                    view()
                self.assertIs(caught.exception, error)
                self.assert_rolled_back()

    def test_django_validation_is_converted_after_rollback(self):
        @api_view(["POST"])
        @permission_classes([AllowAny])
        @atomic
        def view(request):
            self.create()
            raise DjangoValidationError({"name": ["Invalid name"]})

        response = view(APIRequestFactory().post("/"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["name"], ["Invalid name"])
        self.assert_rolled_back()

    def test_json_rendering_failure_rolls_back(self):
        @atomic
        def view():
            self.create()
            return Response({"unsupported": object()})

        with self.assertRaises(TypeError):
            view()
        self.assert_rolled_back()

    def test_finalized_response_is_rendered_before_commit(self):
        @atomic
        @api_view(["POST"])
        @permission_classes([AllowAny])
        def view(request):
            self.create()
            return Response({"ok": True})

        response = view(APIRequestFactory().post("/"))
        self.assertTrue(response.is_rendered)
        self.assertEqual(response.content, b'{"ok":true}')

    def test_finalized_response_renderer_failure_rolls_back(self):
        class BrokenRenderer(JSONRenderer):
            def render(self, *args, **kwargs):
                raise RuntimeError("render failed")

        @atomic
        def view():
            self.create()
            response = Response({"ok": True})
            response.accepted_renderer = BrokenRenderer()
            response.accepted_media_type = "application/json"
            response.renderer_context = {}
            return response

        with self.assertRaisesMessage(RuntimeError, "render failed"):
            view()
        self.assert_rolled_back()

    def test_on_commit_is_discarded_on_error(self):
        callbacks = []

        @atomic
        def view(status):
            self.create()
            transaction.on_commit(lambda: callbacks.append("committed"))
            return Response(status=status)

        view(400)
        self.assertEqual(callbacks, [])
        view(200)
        self.assertEqual(callbacks, ["committed"])

    def test_nested_failure_preserves_outer_transaction(self):
        @atomic
        def view():
            self.create()
            return Response(status=400)

        with transaction.atomic():
            Group.objects.create(name="outer")
            view()
            self.assert_rolled_back()
            self.assertTrue(Group.objects.filter(name="outer").exists())

    def test_using_and_savepoint_are_forwarded(self):
        original = transaction.atomic
        with patch(
            "common.api.decorators.transaction.atomic", wraps=original
        ) as context:
            atomic(lambda: None, using="default", savepoint=False)()
        context.assert_called_once_with(using="default", savepoint=False)

    def test_helper_is_opt_in(self):
        @api_view_with_serializer(["POST"], permission_classes=[AllowAny])
        def view(request):
            self.create()
            return Response(status=400)

        self.assertEqual(view(APIRequestFactory().post("/")).status_code, 400)
        self.assertTrue(Group.objects.filter(name="atomic-test").exists())

    def test_helper_rolls_back_returned_error(self):
        @api_view_with_serializer(["POST"], atomic=True, permission_classes=[AllowAny])
        def view(request):
            self.create()
            return Response(status=409)

        self.assertEqual(view(APIRequestFactory().post("/")).status_code, 409)
        self.assert_rolled_back()

    def test_helper_includes_input_validation(self):
        class Input(serializers.Serializer):
            name = serializers.CharField()

            def validate_name(inner_self, value):
                self.create()
                raise ValidationError("Invalid")

        @api_view_with_serializer(
            ["POST"], input_serializer=Input, atomic=True, permission_classes=[AllowAny]
        )
        def view(request):
            self.fail("Invalid data must not reach the view")

        response = view(APIRequestFactory().post("/", {"name": "test"}))
        self.assertEqual(response.status_code, 400)
        self.assert_rolled_back()

    def test_helper_does_not_swallow_serialization_errors(self):
        class Output(serializers.Serializer):
            name = serializers.SerializerMethodField()

            def get_name(inner_self, value):
                raise RuntimeError("serialization failed")

        @api_view_with_serializer(
            ["POST"], serializer=Output, atomic=True, permission_classes=[AllowAny]
        )
        def view(request):
            return self.create()

        with self.assertRaisesMessage(RuntimeError, "serialization failed"):
            view(APIRequestFactory().post("/"))
        self.assert_rolled_back()

    def test_helper_output_is_serialized_inside_transaction(self):
        class Output(serializers.Serializer):
            name = serializers.SerializerMethodField()

            def get_name(inner_self, value):
                self.assertTrue(transaction.get_connection().in_atomic_block)
                return value.name

        @api_view_with_serializer(
            ["POST"], serializer=Output, atomic=True, permission_classes=[AllowAny]
        )
        def view(request):
            return self.create()

        response = view(APIRequestFactory().post("/"))
        self.assertEqual(response.data, {"name": "atomic-test"})

    def test_helper_json_failure_rolls_back(self):
        @api_view_with_serializer(["POST"], atomic=True, permission_classes=[AllowAny])
        def view(request):
            self.create()
            return {"unsupported": object()}

        with self.assertRaises(TypeError):
            view(APIRequestFactory().post("/"))
        self.assert_rolled_back()

    def test_helper_django_validation_returns_400(self):
        @api_view_with_serializer(["POST"], atomic=True, permission_classes=[AllowAny])
        def view(request):
            self.create()
            raise DjangoValidationError("Invalid operation")

        response = view(APIRequestFactory().post("/"))
        self.assertEqual(response.status_code, 400)
        self.assert_rolled_back()
