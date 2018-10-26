# coding: utf-8
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.views.debug import get_safe_settings
from django.views.decorators.cache import never_cache

from common.api.api_views import get_urls, user_infos
from common.utils import json_encode, render_to


@never_cache
@render_to('common/cache.html')
@login_required
def view_cache(request):
    """
    Cache
    """
    value = None
    key = request.GET.get('key', None)
    if key:
        value = cache.get(key)
        try:
            value = dict(value)
            value = json_encode(value, indent=4)
        except (TypeError, ValueError):
            pass
    if hasattr(cache, 'keys'):
        for key in request.POST:
            cache.delete_pattern(key)
        keys = sorted(cache.keys('*'))
    else:
        for key in request.POST:
            cache.delete(key)
        keys = sorted((key.split(':')[-1] for key in cache._cache.keys()))

    return {
        'keys': keys,
        'value': value or None,
    }


@never_cache
@render_to('common/scripts.js')
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
        'urls': json_encode(get_urls(request).data),
        'user': json_encode(user_infos(request).data),
        'context': json_encode(context),
    }
