# coding: utf-8
from django.core.management.base import BaseCommand

from ...websocket import run_websocket_server


class Command(BaseCommand):

    def handle(self, *args, **options):
        run_websocket_server()
