# coding: utf-8
import logging

from common.utils import abort_query
from django.core.management.base import BaseCommand
from django.utils.translation import ugettext_lazy as _


# Logging
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Interrompt une ou plusieurs requêtes correspondant au nom de l'application"
    leave_locale_alone = True

    def add_arguments(self, parser):
        parser.add_argument('name', type=str, help=_("Nom de l'application"))
        parser.add_argument('--kill', dest='kill', action='store_true', help=_("Tue la requête au lieu de l'annuler"))
        parser.add_argument('--using', dest='using', type=str, help=_("Nom de la base de donnée ciblée"))
        parser.add_argument('--timeout', dest='timeout', type=int, help=_("Timeout"))

    def handle(self, name, kill=False, using=None, timeout=None, **options):
        count = abort_query(name, kill=kill, using=using, timeout=timeout)
        logger.info(_("{} requête(s) supprimée(s).").format(count))
