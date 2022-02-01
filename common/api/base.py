# coding: utf-8
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from common.api.permissions import CurrentUserPermissions
from common.api.serializers import UserSerializer
from common.api.utils import create_api, disable_relation_fields
from common.api.viewsets import UserViewSet
from common.models import MODELS, GroupMetaData, MetaData, UserMetaData
from common.settings import settings
from common.utils import _assert

try:
    from rest_framework.authtoken.models import Token

    _assert("rest_framework.authtoken" in settings.INSTALLED_APPS)
except (AssertionError, ImportError):
    Token = None

# Modèle utilisateur courant
User = get_user_model()

# Serializers et viewsets crées par défaut
SERIALIZERS = {}
VIEWSETS = {}

# Héritages des serializers et viewsets
SERIALIZERS_BASE = {
    User: (UserSerializer,),
    Group: (),
    UserMetaData: (),
    GroupMetaData: (),
    Permission: (),
    ContentType: (),
    LogEntry: (),
    Token: (),
}
VIEWSETS_BASE = {
    User: (UserViewSet,),
}

# Données complémentaires à ajouter aux serializers et viewsets
SERIALIZERS_DATA = {}
VIEWSETS_DATA = {}

# Surcharges du queryset du viewset principal
QUERYSETS = {}

# Métadonnées des serializers
METADATA = {}

# Configuration des serializers
CONFIGS = {
    MetaData: {},
    Group: dict(many_to_many=True, depth=1, permissions=[CurrentUserPermissions]),
    GroupMetaData: dict(permissions=[CurrentUserPermissions]),
    User: dict(many_to_many=True, depth=1, permissions=[CurrentUserPermissions]),
    UserMetaData: dict(permissions=[CurrentUserPermissions]),
}

# Configuration par défaut
DEFAULT_CONFIG = {}

# Précise les filtres à appliquer sur les permissions spécifiques par utilisateur
CurrentUserPermissions.filters.update(
    {
        User: lambda request: dict(id=request.user.pk),
        Group: lambda request: dict(user=request.user),
        UserMetaData: lambda request: dict(user=request.user),
        GroupMetaData: lambda request: dict(group__user=request.user),
    }
)

# Désactive les listes déroulantes sur les champs de relations
disable_relation_fields(User, Group, Permission, ContentType, LogEntry, Token, *MODELS)

# Création des APIs REST standard pour les modèles de cette application
router, *_ = create_api(User, Group, Permission, ContentType, LogEntry, Token, *MODELS)
