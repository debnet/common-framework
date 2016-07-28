# coding: utf-8
from django.conf.urls import url
from django.utils.translation import ugettext_lazy as _

from common import views


urlpatterns = ([
    url(_(r'^cache/$'), views.view_cache, name='cache'),
    url(_(r'^scripts/$'), views.scripts, name='scripts'),
], 'common')
