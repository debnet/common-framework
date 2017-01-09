# coding: utf-8
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Group
from django.utils.translation import ugettext_lazy as _

from common.settings import settings


# Logging
logger = logging.getLogger(__name__)


class LdapAuthenticationBackend(ModelBackend):
    """
    Authentification via LDAP
    """
    def authenticate(self, username=None, password=None, **kwargs):
        if not getattr(settings, 'LDAP_ENABLE', True) or len(password) == 0:
            return None

        try:
            # Connexion au serveur LDAP
            import ldap3 as ldap
            ldap_server = ldap.Server(settings.LDAP_HOST)

            # Connexion de l'utilisateur dans le LDAP
            login = settings.LDAP_LOGIN.format(username=username)
            ldap_connection = ldap.Connection(ldap_server, user=login, password=password, auto_bind=True)
            with ldap_connection:
                ldap_connection.bind()

                # Récupération des informations de l'utilisateur
                filter = settings.LDAP_FILTER.format(username=username)
                if ldap_connection.search(
                        settings.LDAP_BASE,
                        filter,
                        attributes=settings.LDAP_ATTRIBUTES):
                    attributes = ldap_connection.response[0].get('attributes', {})
                    User = get_user_model()
                    try:
                        user = User.objects.get(username=username)
                        user.set_password(password)
                    except User.DoesNotExist:
                        user = User(
                            username=username,
                            password=password,
                            first_name=attributes['givenName'],
                            last_name=attributes['sn'],
                            email=attributes['mail'],
                            is_active=True,
                            is_staff=True,
                        )

                    # Vérification du statut de l'utilisateur
                    user.is_superuser = False
                    group_names = [group.split(',')[0].split('=')[1] for group in attributes['memberOf']]
                    if username in settings.LDAP_ADMIN_USERS or set(group_names) & set(settings.LDAP_ADMIN_GROUPS):
                        user.is_superuser = True
                    user.save()

                    # Récupération des groupes de l'utilisateur
                    non_ldap_groups = list(user.groups.exclude(name__startswith=settings.LDAP_GROUP_PREFIX))
                    user.groups.clear()
                    for group_name in group_names:
                        group, created = Group.objects.get_or_create(
                            name='{}{}'.format(settings.LDAP_GROUP_PREFIX, group_name))
                        user.groups.add(group)
                    user.groups.add(*non_ldap_groups)
                    return user

                logger.info(_("Utilisateur {username} non trouvé dans le répertoire LDAP.").format(username=username))
                return None
        except Exception as erreur:
            logger.warning(_("Erreur lors de l'authentification LDAP : {erreur}").format(erreur=erreur))
            return None
