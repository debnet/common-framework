# coding: utf-8
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.views.debug import CallableSettingWrapper
from django.views.decorators.cache import never_cache

from common.api.api_views import get_urls, user_infos
from common.utils import json_encode, render_to


@never_cache
@render_to("common/cache.html")
@login_required
def view_cache(request):
    """
    Cache
    """
    if not request.user.is_superuser:
        return {}

    key, value, excludes = request.GET.get("key", None), None, request.GET.getlist("exclude")

    if key:
        if not hasattr(cache, "keys"):
            key = ":".join(key.split(":")[2:])
        value = cache.get(key)
        if value:
            try:
                value = dict(value)
                value = json_encode(value, indent=4)
            except (TypeError, ValueError):
                pass

    if hasattr(cache, "keys"):
        for key in request.POST:
            cache.delete_pattern(key)
        keys = cache.keys("*")
    else:
        for key in request.POST:
            key = ":".join(key.split(":")[2:])
            cache.delete(key)
        keys = cache._cache.keys()

    return {
        "keys": sorted(key for key in keys if not any(exclude in key for exclude in excludes)),
        "value": value,
    }


@never_cache
@render_to("common/scripts.js", content_type="text/javascript")
def scripts(request):
    """
    Scripts communs:
    """
    context = {}
    for key, value in get_safe_settings().items():
        try:
            json_encode(value)
        except TypeError:
            continue
        context[key] = value

    return {
        "urls": json_encode(get_urls(request).data),
        "user": json_encode(user_infos(request).data),
        "context": json_encode(context),
    }


HIDDEN_SETTINGS = re.compile("API|TOKEN|KEY|SECRET|PASS|SIGNATURE", flags=re.IGNORECASE)
CLEANSED_SUBSTITUTE = "**********"


def cleanse_setting(key, value):
    """
    Cleanse an individual setting key/value of sensitive content. If the value
    is a dictionary, recursively cleanse the keys in that dictionary.
    """
    try:
        if HIDDEN_SETTINGS.search(key):
            cleansed = CLEANSED_SUBSTITUTE
        else:
            if isinstance(value, dict):
                cleansed = {k: cleanse_setting(k, v) for k, v in value.items()}
            else:
                cleansed = value
    except TypeError:
        cleansed = value

    if callable(cleansed):
        cleansed = CallableSettingWrapper(cleansed)
    return cleansed


def get_safe_settings():
    """
    Return a dictionary of the settings module with values of sensitive
    settings replaced with stars (*********).
    """
    settings_dict = {}
    for k in dir(settings):
        if k.isupper():
            settings_dict[k] = cleanse_setting(k, getattr(settings, k))
    return settings_dict
