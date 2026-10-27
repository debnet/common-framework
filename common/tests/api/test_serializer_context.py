# coding: utf-8
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory, force_authenticate

from common.api.utils import api_view_with_serializer


class InputSerializerContextTests(TestCase):
    def test_post_receives_drf_context_with_or_without_atomic(self):
        for atomic in (False, True):
            with self.subTest(atomic=atomic):
                captured = {}

                class Input(serializers.Serializer):
                    user = serializers.HiddenField(
                        default=serializers.CurrentUserDefault()
                    )

                    def validate(self, attrs):
                        captured.update(self.context)
                        return attrs

                @api_view_with_serializer(
                    ["POST"],
                    input_serializer=Input,
                    atomic=atomic,
                    permission_classes=[AllowAny],
                )
                def view(request):
                    self.assertIs(request.validated_data["user"], user)
                    return {"ok": True}

                user = get_user_model()(username="context-user")
                request = APIRequestFactory().post("/", {}, format="json")
                force_authenticate(request, user=user)
                response = view(request)
                self.assertEqual(response.status_code, 200)
                self.assertIs(captured["request"].user, user)
                self.assertIsInstance(captured["view"], view.view_class)
                self.assertIs(captured["view"].request, captured["request"])
                self.assertIsNone(captured["format"])

    def test_context_override_is_used(self):
        captured = {}

        class Input(serializers.Serializer):
            def validate(self, attrs):
                captured.update(self.context)
                return attrs

        def get_context(view):
            return {**GenericAPIView.get_serializer_context(view), "custom": "value"}

        @api_view_with_serializer(
            ["POST"],
            input_serializer=Input,
            permission_classes=[AllowAny],
            get_serializer_context=get_context,
        )
        def view(request, format=None):
            return {"ok": True}

        response = view(APIRequestFactory().post("/"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["custom"], "value")
        self.assertEqual(captured["format"], "json")

    def test_put_only_receives_context_and_preserves_instance_and_partial(self):
        captured = {}
        instance = {"name": "before"}

        class Input(serializers.Serializer):
            name = serializers.CharField()

            def validate(self, attrs):
                captured.update(self.context)
                captured["instance"] = self.instance
                captured["partial"] = self.partial
                return attrs

        @api_view_with_serializer(
            ["PUT"],
            input_serializer=Input,
            permission_classes=[AllowAny],
            get_object=lambda view: instance,
        )
        def view(request):
            return request.validated_data

        response = view(APIRequestFactory().put("/", {}, format="json"), partial=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {})
        self.assertIsInstance(captured["view"], view.view_class)
        self.assertIs(captured["request"], captured["view"].request)
        self.assertIs(captured["instance"], instance)
        self.assertTrue(captured["partial"])
