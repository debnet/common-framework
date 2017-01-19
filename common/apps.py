# coding: utf-8
from django.apps import AppConfig
from django.utils.translation import ugettext_lazy as _


class CommonConfig(AppConfig):
    name = 'common'
    verbose_name = _("Common Framework")

    def ready(self):
        # Force la surcharge du lookup "unaccent" sur les champs texte
        from django.db.models import CharField, TextField
        from common.fields import CustomUnaccent
        CharField.register_lookup(CustomUnaccent)
        TextField.register_lookup(CustomUnaccent)
