"""project URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken import views as drf_views

from common.urls import urlpatterns as view_urlpatterns
from common.api.urls import urlpatterns as api_urlpatterns


urlpatterns = [
    path('admin/', admin.site.urls),

    # Django REST Framework
    path('api/', include('rest_framework.urls', namespace='drf')),

    # Django REST Framework Auth Token
    path('api/auth/', drf_views.obtain_auth_token, name='token'),

    # Common Framework
    path('common/', include(view_urlpatterns, namespace='common')),
    path('api/common/', include(api_urlpatterns, namespace='common-api')),
]
