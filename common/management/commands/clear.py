# coding: utf-8
import logging

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.translation import gettext as _

from common.models import Entity

# Logging
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = _("Supprime les données de l'application")
    leave_locale_alone = True

    def add_arguments(self, parser):
        parser.add_argument("app_label", type=str, help=_("Nom de l'application"))
        parser.add_argument("--excludes", dest="excludes", type=str, nargs="+", help=_("Modèle(s) à exclure"))
        parser.add_argument("--includes", dest="includes", type=str, nargs="+", help=_("Modèle(s) à inclure"))

    @transaction.atomic
    def handle(self, *args, app_label=None, excludes=None, includes=None, **options):
        excludes = excludes or []
        includes = includes or []
        if app_label:
            app = apps.get_app_config(app_label)
            models = app.models.values()
        else:
            models = (model for model_name, model in apps.get_models())
        for model in models:
            object_name = model._meta.object_name
            if excludes and object_name in excludes:
                continue
            if includes and object_name not in includes:
                continue
            if isinstance(model, Entity):
                count = model.objects.all().delete(_ignore_log=True)
            else:
                count = model.objects.all().count()
                model.objects.all().delete()
            model_name = str(model._meta.verbose_name) if count == 1 else str(model._meta.verbose_name_plural)
            logger.info(_("{} {} supprimé(s).").format(count, model_name))
