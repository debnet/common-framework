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
    def authenticate(self, request=None, username=None, password=None, **kwargs):
        if settings.LDAP_ENABLE is False or len(password) == 0:
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

                    # Création de l'utilisateur en base de données
                    User = get_user_model()
                    username = username and username.lower()  # Nom d'utilisateur en minuscules par défaut
                    user, created = User.objects.get_or_create(username=username)
                    user.set_password(password)

                    # Méthode utilitaire pour tenter de remplir les champs par défaut de l'utilisateur
                    def set_value(obj, name, value):
                        item = getattr(type(obj), name, None)
                        if item and not isinstance(item, property):
                            setattr(obj, name, value)

                    # Informations
                    set_value(user, 'first_name', attributes['givenName'])
                    set_value(user, 'last_name', attributes['sn'])
                    set_value(user, 'email', attributes['mail'])
                    set_value(user, 'is_active', True)

                    # Vérification du statut de l'utilisateur
                    set_value(user, 'is_superuser', False)
                    set_value(user, 'is_staff', False)
                    group_names = [group.split(',')[0].split('=')[1] for group in attributes['memberOf']]
                    if username in settings.LDAP_ADMIN_USERS or set(group_names) & set(settings.LDAP_ADMIN_GROUPS):
                        set_value(user, 'is_superuser', True)
                        set_value(user, 'is_staff', True)
                    if username in settings.LDAP_STAFF_USERS or set(group_names) & set(settings.LDAP_STAFF_GROUPS):
                        set_value(user, 'is_staff', True)
                    user.save()

                    # Récupération des groupes de l'utilisateur
                    if hasattr(User, 'groups'):
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
