# coding: utf-8
from django.urls import include, path
from rest_framework.authtoken import views as drf_views


urlpatterns = [
    # Django REST Framework
    path('api/', include('rest_framework.urls', namespace='drf')),

    # Django REST Framework Auth Token
    path('api/auth/', drf_views.obtain_auth_token, name='token'),

    # Common Framework
    path('common/', include('common.urls', namespace='common')),
    path('api/common/', include('common.api.urls', namespace='common-api')),
]
