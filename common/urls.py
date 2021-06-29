# coding: utf-8
from django.urls import path

from common import views

namespace = "common"
app_name = "common"
urlpatterns = [
    path("cache/", views.view_cache, name="cache"),
    path("scripts.js", views.scripts, name="scripts"),
]
urls = (urlpatterns, namespace, app_name)

try:
    from rest_framework.schemas import get_schema_view

    urlpatterns.append(path("schema/", get_schema_view()))
except (AssertionError, ImportError):
    pass
