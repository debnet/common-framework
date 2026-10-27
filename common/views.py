# coding: utf-8
"""Vues HTML/JavaScript : inspection du cache et configuration publique explicite."""

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.views.decorators.cache import never_cache

from common.api.api_views import get_urls, user_infos
from common.settings import settings
from common.utils import json_encode, render_to


@never_cache
@render_to("common/cache.html")
@login_required
def view_cache(request):
    """
    Inspecte le cache et permet sa purge aux seuls superusers connectés.

    GET sélectionne une clé ; POST supprime les clés/motifs soumis. Les autres
    utilisateurs connectés obtiennent un contexte vide. Certaines branches
    utilisent des API privées du cache local ou les extensions django-redis.
    """
    if not request.user.is_superuser:
        return {}

    key, value, excludes = (
        request.GET.get("key", None),
        None,
        request.GET.getlist("exclude"),
    )

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
        "keys": sorted(
            key for key in keys if not any(exclude in key for exclude in excludes)
        ),
        "value": value,
    }


@never_cache
@render_to("common/scripts.js", content_type="text/javascript")
def scripts(request):
    """
    Rend le helper JavaScript avec routes, profil courant et settings autorisés.

    Les settings sont exclusivement ceux de PUBLIC_SETTINGS ; toute valeur
    ajoutée à cette liste devient publique. Les valeurs non sérialisables sont
    omises. Le profil est obtenu via user_infos et la réponse ne doit pas être
    mise en cache, car son contenu peut dépendre de l'utilisateur connecté.
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


def get_safe_settings():
    """
    Retourne uniquement les settings explicitement autorisés pour le navigateur.
    Toute valeur ajoutée à PUBLIC_SETTINGS doit être considérée comme publique.
    """
    return {key: getattr(settings, key) for key in settings.PUBLIC_SETTINGS}
