from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken import views as drf_views

urlpatterns = [
    path(r"admin/", admin.site.urls),
    # Django REST Framework
    path(r"api/", include("rest_framework.urls", namespace="drf")),
    # Django REST Framework Auth Token
    path(r"api/auth/", drf_views.obtain_auth_token, name="token"),
    # Common Framework
    path(r"common/", include("common.urls", namespace="common")),
    path(r"api/common/", include("common.api.urls", namespace="common-api")),
]
