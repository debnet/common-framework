# coding: utf-8
from concurrent.futures import ThreadPoolExecutor

from django.test import TestCase, override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from common.api.utils import (
    api_paginate,
    api_view_with_serializer,
    create_model_serializer_and_viewset,
)
from common.models import Webhook

Serializer, ViewSet = create_model_serializer_and_viewset(Webhook, hyperlinked=False)


@api_view_with_serializer(["GET"])
def entries(request):
    return api_paginate(request, Webhook.objects.all(), Serializer)


@override_settings(ENABLE_API_PERMISSIONS=False, SERVICE_USAGE=False)
class SerializerIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        cls.user = get_user_model().objects.create_superuser("serializer-admin")
        Webhook.objects.create(name="Example", url="https://example.com")

    def selected(self, params):
        view = ViewSet()
        view.action = "list"
        view.request = Request(APIRequestFactory().get("/", params))
        return view.get_serializer_class()

    def test_annotations_do_not_mutate_shared_classes(self):
        for simple in (False, True):
            params = {"lower": "name|audit_label"}
            if simple:
                params["simple"] = "true"
            original = ViewSet.simple_serializer if simple else ViewSet.serializer_class
            original_fields = dict(original._declared_fields)
            selected = self.selected(params)
            self.assertIsNot(selected, original)
            self.assertTrue(issubclass(selected, original))
            self.assertIs(selected.Meta, original.Meta)
            self.assertIn("audit_label", selected._declared_fields)
            self.assertEqual(original._declared_fields, original_fields)
            clean = self.selected({"simple": "true"} if simple else {})
            self.assertNotIn("audit_label", clean._declared_fields)

    def test_simultaneous_annotations_are_isolated(self):
        def select(alias):
            return self.selected({"lower": "name|" + alias})

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(select, ("first_label", "second_label")))
        self.assertIn("first_label", first._declared_fields)
        self.assertNotIn("second_label", first._declared_fields)
        self.assertIn("second_label", second._declared_fields)
        self.assertNotIn("first_label", second._declared_fields)

    def test_responses_and_api_paginate_remain_isolated(self):
        for endpoint in (ViewSet.as_view({"get": "list"}), entries):
            for params, annotated in (
                ({"lower": "name|audit_label"}, True),
                ({}, False),
            ):
                request = APIRequestFactory().get("/", params)
                force_authenticate(request, user=self.user)
                response = endpoint(request)
                self.assertEqual(response.status_code, 200, response.data)
                row = response.data["results"][0]
                if annotated:
                    self.assertEqual(row["audit_label"], "example")
                else:
                    self.assertNotIn("audit_label", row)
        self.assertNotIn("audit_label", Serializer._declared_fields)
