# coding: utf-8
from django.urls import path
from rest_framework.schemas import get_schema_view

from common import views


urlpatterns = ([
    path('cache/', views.view_cache, name='cache'),
    path('scripts/', views.scripts, name='scripts'),
    path('schema/', get_schema_view()),
], 'common')
