# coding: utf-8
import logging

from common.utils import abort_sql
from django.core.management.base import BaseCommand
from django.utils.translation import ugettext_lazy as _


# Logging
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Interrompt une ou plusieurs connexions à la base de données correspondant au nom de l'application"
    leave_locale_alone = True

    def add_arguments(self, parser):
        parser.add_argument('name', type=str, help=_("Nom de l'application"))
        parser.add_argument('--kill', dest='kill', action='store_true', help=_("Tue la connexion au lieu de l'annuler"))
        parser.add_argument('--using', dest='using', type=str, help=_("Nom de la base de donnée ciblée"))
        parser.add_argument('--timeout', dest='timeout', type=int, help=_("Temps d'exécution maximal"))

    def handle(self, name, kill=False, using=None, timeout=None, **options):
        count = abort_sql(name, kill=kill, using=using, timeout=timeout)
        logger.info(_("{} connexion(s) à la base de données interrompues(s).").format(count))
