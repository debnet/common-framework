# coding: utf-8
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_migrate


def add_view_permissions(sender, **kwargs):
    """
    Complète les permissions view manquantes après une migration.

    Hook historique antérieur aux permissions de lecture natives Django.
    Parcourt les ContentType via le manager par défaut et crée seulement les
    lignes absentes ; ne supprime ni ne réattribue aucune permission existante.
    """
    for content_type in ContentType.objects.all():
        codename = "view_%s" % content_type.model
        if not Permission.objects.filter(content_type=content_type, codename=codename):
            Permission.objects.create(
                content_type=content_type,
                codename=codename,
                name="Can view %s" % content_type.name,
            )


post_migrate.connect(add_view_permissions)
