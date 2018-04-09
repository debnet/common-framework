# coding: utf-8
from django.conf.urls import url

from common.api import api_views
from common.api.base import router


urlpatterns = ([
    url(r'resolve/$', api_views.resolve_url, name='resolve_url'),
    url(r'urls/$', api_views.get_urls, name='get_urls'),
    url(r'user/infos/$', api_views.user_infos, name='user_infos'),
    url(r'user/infos/(?P<user_id>\d+)/$', api_views.user_infos, name='user_infos_by_id'),
    url(r'user/reset_password/$', api_views.reset_password, name='user_reset_password'),
    url(r'user/confirm_password/$', api_views.confirm_password, name='user_confirm_password'),
    url(r'metadata/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/$', api_views.metadata, name='metadata')
] + router.urls, 'common')
