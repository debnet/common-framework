# coding: utf-8
from django.conf import settings as user_settings

from common.utils import singleton


@singleton
class Settings:
    """
    Classe de configuration proxy avec valeurs par défaut
    """

    # Valeurs par défaut
    default = dict(
        IP_DETECTION=False,
        IGNORE_LOG=False,
        NOTIFY_CHANGES=False,
        NOTIFY_OPTIONS={},
        WEBSOCKET_ENABLED=False,
        WEBSOCKET_URL='',
        FRONTEND_SECRET_KEY='',
    )

    def __getattr__(self, item):
        return getattr(user_settings, item, self.default.get(item, None))


# Proxy de configuration
settings = Settings()
