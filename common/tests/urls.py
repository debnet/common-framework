# coding: utf-8
from django.conf.urls import include, url
from django.utils.translation import ugettext_lazy as _
from rest_framework.authtoken import views as drf_views

from common.urls import urlpatterns as view_urlpatterns
from common.api.urls import urlpatterns as api_urlpatterns


urlpatterns = [
    # Django REST Framework
    url(_(r'^api/'), include('rest_framework.urls', namespace='drf')),

    # Django REST Framework Auth Token
    url(_(r'^api/auth/'), drf_views.obtain_auth_token, name='token'),

    # Common Framework
    url(_(r'^common/'), include(view_urlpatterns, namespace='common')),
    url(_(r'^api/common/'), include(api_urlpatterns, namespace='common-api')),
]
