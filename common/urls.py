# coding: utf-8
from django.urls import path

from common import views


urlpatterns = ([
    path('cache/', views.view_cache, name='cache'),
    path('scripts/', views.scripts, name='scripts'),
], 'common')
