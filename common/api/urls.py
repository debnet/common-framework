# coding: utf-8
from django.conf.urls import url
from django.utils.translation import ugettext_lazy as _

from common.api import api_views
from common.api.base import router


urlpatterns = ([
    url(_(r'^resolve/$'), api_views.resolve_url, name='resolve_url'),
    url(_(r'^urls/$'), api_views.get_urls, name='get_urls'),
    url(_(r'^user/infos/$'), api_views.user_infos, name='user_infos'),
    url(_(r'^user/infos/(?P<user_id>\d+)/$'), api_views.user_infos, name='user_infos_by_id'),
    url(_(r'^user/reset_password/$'), api_views.reset_password, name='user_reset_password'),
    url(_(r'^user/confirm_password/$'), api_views.confirm_password, name='user_confirm_password'),
    url(_(r'^metadata/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/$'), api_views.metadata, name='metadata')
] + router.urls, 'common')
