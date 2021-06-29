# coding: utf-8
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CommonConfig(AppConfig):
    name = "common"
    verbose_name = _("Common Framework")
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        # Force la surcharge du lookup "unaccent" sur les champs texte
        from django.db.models import CharField, TextField

        try:
            from common.fields import CustomUnaccent

            CharField.register_lookup(CustomUnaccent)
            TextField.register_lookup(CustomUnaccent)
        except ImportError:
            pass
