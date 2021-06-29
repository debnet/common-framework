# coding: utf-8
from django.urls import path

from common.api import api_views
from common.api.base import router

namespace = "common-api"
app_name = "common"
urlpatterns = [
    path(r"resolve/", api_views.resolve_url, name="resolve_url"),
    path(r"urls/", api_views.get_urls, name="get_urls"),
    path(r"user/infos/", api_views.user_infos, name="user_infos"),
    path(r"user/infos/<int:user_id>/", api_views.user_infos, name="user_infos_by_id"),
    path(r"user/reset_password/", api_views.reset_password, name="user_reset_password"),
    path(r"user/confirm_password/", api_views.confirm_password, name="user_confirm_password"),
    path(r"metadata/<uuid:uuid>/", api_views.metadata, name="metadata"),
] + router.urls
urls = (urlpatterns, namespace, app_name)
