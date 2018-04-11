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
        IGNORE_GLOBAL=False,
        NOTIFY_CHANGES=False,
        NOTIFY_OPTIONS={},
        WEBSOCKET_ENABLED=False,
        WEBSOCKET_URL='',
        FRONTEND_SECRET_KEY='',
        # LDAP
        LDAP_ENABLE=False,
        LDAP_LOGIN='',
        LDAP_HOST='',
        LDAP_BASE='',
        LDAP_FILTER='',
        LDAP_ATTRIBUTES=[],
        LDAP_ADMIN_USERS=[],
        LDAP_STAFF_USERS=[],
        LDAP_ADMIN_GROUPS=[],
        LDAP_STAFF_GROUPS=[],
        LDAP_GROUP_PREFIX=''
    )

    def __getattr__(self, item):
        return getattr(user_settings, item, self.default.get(item, None))


# Proxy de configuration
settings = Settings()
