# coding: utf-8
from django.urls import include, path
from rest_framework.authtoken import views as drf_views

from common.urls import urlpatterns as view_urlpatterns
from common.api.urls import urlpatterns as api_urlpatterns


urlpatterns = [
    # Django REST Framework
    path('api/', include('rest_framework.urls', namespace='drf')),

    # Django REST Framework Auth Token
    path('api/auth/', drf_views.obtain_auth_token, name='token'),

    # Common Framework
    path('common/', include(view_urlpatterns, namespace='common')),
    path('api/common/', include(api_urlpatterns, namespace='common-api')),
]
