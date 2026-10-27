# Framework commun

Boîte à outils pour **Django et Django REST Framework** : modèles historisés,
métadonnées, API CRUD dynamiques, formulaires, administration et utilitaires.

La version courante cible **Python 3.13+ et Django 6.0+**, avec une version de DRF
compatible. La suite a été vérifiée sur Python 3.13 avec Django 6.0.9 et 6.1.2 ;
les branches legacy conservées ne constituent pas une promesse de support des anciennes versions.

## Installation et démarrage

```bash
python -m pip install common-framework
```

Conserver les applications Django standard du projet généré (notamment
`django.contrib.admin`, requis par les API communes), ajouter `"rest_framework"`
et `"common"` à `INSTALLED_APPS`, puis appliquer les migrations avec
`python manage.py migrate`. Définir aussi `REST_FRAMEWORK = {}` dans les settings
si aucune configuration DRF n'existe encore.

```python
from django.db import models
from common.models import Entity


class Personne(Entity):
    nom = models.CharField(max_length=100)
```

Pour générer une API, dans le module d'URLs de l'application :

```python
from common.api.utils import create_api
from monapp.models import Personne

app_name = "monapp-api"
router, serializers, viewsets = create_api(Personne)
urlpatterns = router.urls
```

Inclure ensuite ce module dans les URLs du projet. Les routes utilitaires sont
disponibles séparément via `common.urls` et `common.api.urls`.
Les signatures, options avancées et points d'extension sont documentés dans les docstrings.

## Modèles et données

Dans `common.models` :

| Composant | Fonctionnalités |
|---|---|
| `CommonModel`, `CommonQuerySet` | Métadonnées, suivi des différences, conversion en dictionnaire et sérialisation. |
| `Entity`, `EntityQuerySet` | Historisation des créations, modifications, suppressions et relations M2M ; acteur, motif et restauration. |
| `PerishableEntity`, `PerishableEntityQuerySet` | Versions successives d'une entité et périodes de validité. |
| `History`, `HistoryField` | Consultation des événements et des anciennes/nouvelles valeurs. |
| `Global` | Référentiel d'entités par UUID, indépendamment du modèle. |
| `MetaData` | Métadonnées par clé, avec validité et expiration. |
| `UserMetaData`, `GroupMetaData` | Données JSON associées aux comptes et groupes ; fusion des données de groupe et de compte. |
| `Webhook`, `BaseWebhook` | Notifications HTTP des événements, filtres de modèles/actions et authentification distante. |
| `ServiceUsage` | Compteurs d'accès, limites et périodes de remise à zéro. |
| `CustomGenericForeignKey`, `CustomGenericRelation` | Relations génériques adaptées aux identifiants Common. |
| `Serialized`, `from_dict`, `to_dict`, `model_to_dict` | Sérialisation Django et conversion/reconstruction de modèles. |

L'historisation et les notifications peuvent utiliser Celery, ou s'exécuter
synchroniquement sans broker. Le projet hôte configure l'application, le broker
et les workers ; les tâches sont exposées dans `common.tasks`.

### Champs, formulaires et administration

- **`common.fields`** : `CustomDecimalField`, `PickleField`, `JsonField` et lookups JSON complémentaires.
- **`common.forms`** : `CommonForm`, `CommonFormSet`, `CommonModelForm`,
  `CommonModelFormSet`, `CommonInlineFormSet` et leurs bases ; contexte et acteur
  transmis aux formulaires imbriqués.
- **Fabriques de formulaires** : `get_formset`, `get_model_formset`, `get_model_form`.
- **Recherche et JSON** : `BaseFilterForm` pour traduire des critères en filtres ORM ;
  `common.forms.JsonField` avec les codecs JSON Common par défaut.
- **`common.admin`** : administrations et inlines pour les modèles Common,
  consultation des métadonnées/historiques, restauration et génération avec `create_admin`.

## API REST

Les API générées donnent accès au CRUD et à un langage de requête avancé :
filtres et relations, combinaisons logiques, tris, projections de champs,
annotations, agrégations, regroupements, pagination et mémorisation des paramètres de recherche.
Elles sont destinées aux usages nécessitant un accès riche à l'ORM.

| Module | Composants utilisables |
|---|---|
| `common.api.utils` | `create_api`, `create_model_serializer_and_viewset`, `create_model_serializer`, `to_model_serializer`, `to_model_viewset`. |
| `common.api.utils` | Vues avec `api_view_with_serializer`, `auto_view`, `perishable_view` ; pagination avec `api_paginate` ; `disable_relation_fields` pour l'interface DRF. |
| `common.api.decorators` | `atomic` : transaction des vues synchrones, rollback sur exception ou réponse HTTP d'erreur, contrôle du rendu avant commit. |
| `common.api.viewsets` | `CommonModelViewSet` et gestion des comptes avec `UserViewSet`. |
| `common.api.serializers` | `CommonModelSerializer`, `GenericFormSerializer`, serializers de comptes et métadonnées. |
| `common.api.fields` | `JsonField`, `QuerySetChoiceField`, `ChoiceDisplayField`, `ReadOnlyObjectField`, champs `CustomHyperlinked*`, `AsymetricRelatedField` (objet en lecture, identifiant en écriture). |
| `common.api.permissions` | Permissions de modèles, lecture de ses propres données et `ServiceUsagePermission`. |
| `common.api.pagination` | `CustomPageNumberPagination` : navigation enrichie et taille plafonnée. |
| `common.api.renderers` | `CustomCSVRenderer`, si la dépendance CSV optionnelle est installée. |
| `common.api.base` | Routeur et registres partagés de configuration des API. |

`common.api.urls` expose aussi la résolution/liste des routes, le profil du
compte connecté et les métadonnées d'une entité par UUID. La consultation d'un
autre compte est réservée au staff/superuser ; les métadonnées exigent les
permissions correspondant aux opérations demandées.

**Permissions :** les contrôles portent sur les modèles, y compris les modèles
traversés par les agrégations. Ce n'est pas une politique d'accès par ligne ou
par champ ; prévoir des vues/serializers dédiés pour les ressources restreintes.

### Vues transactionnelles

```python
from common.api.utils import api_view_with_serializer


@api_view_with_serializer(["POST"], atomic=True)
def modifier(request):
    # Écritures et sérialisation dans la même transaction.
    return {"ok": True}
```

L'option est désactivée par défaut ; `using="alias"` choisit la base.
Le décorateur `common.api.decorators.atomic` s'utilise aussi seul, sous `@api_view`.
Les exceptions et réponses HTTP >= 400 annulent les écritures. Avant finalisation
DRF, le contrôle du rendu porte sur JSON uniquement ; ni streaming ni effets
externes ne sont transactionnels. Aucun mode simulation implicite n'est ajouté.

## Utilitaires Python et Django

Les fonctions ci-dessous sont disponibles dans **`common.utils`**.

| Domaine | Utilitaires |
|---|---|
| Décorateurs et tâches | `singleton`, `timed_cache`, `timeit`, `synchronized`, `get_current_app`, `CeleryFake`. |
| Dates et nombres | `parsedate`, `str_to_bool`, `str_to_num`, `decimal`, `decimal_to_str`. |
| Collections | `recursive_dict_product`, `sort_dict`, `merge_dict`, `CustomDict`, `idict`, `null`. |
| Conversion d'objets | `to_tuple`, `to_namedtuple`, `to_object`, `to_dict` et leurs alias historiques ; `get_size`. |
| JSON et encodages | `JsonEncoder`, `JsonDecoder`, `json_encode`, `json_decode`, `base64_encode`, `base64_decode`, `short_identifier`. |
| Vues et fichiers | `render_to`, `ajax_request`, `temporary_upload`, `TemporaryFile`, `TemporaryFileHandler`, `DownloadFile`, `download_file`. |
| Inspection et archives | `file_is_text`, `seek_end`, `hash_file`, `process_file` (attente de stabilisation et extraction ZIP/TAR). |
| Introspection ORM | `get_choices_fields`, `get_prefetchs`, `get_related`, `get_field_by_path`, `prefetch_generics`, `prefetch_metadata`, `get_pk_field`, `get_all_models`, `get_models_from_queryset`. |
| Permissions et suppression | `get_all_permissions`, `get_model_permissions`, `collect_deleted_data`. |
| HTTP et identité | `recursive_get_urls`, `get_current_user`, `get_client_ip`, `is_valid_ipv4`, `is_valid_ipv6`, `is_valid_ip`. |
| Services | `reserve_service_usage`, `send_mail`, `merge_validation_errors`, `web_to_raw_tsquery`, `abort_sql`. |
| Configuration et code de confiance | `patch_settings`, `evaluate`, `execute`. |

### Autres modules

- **`common.excel.ImportExport`** : import/export Excel de modèles, références,
  listes de validation et métadonnées.
- **`common.commands.ImportExcelCommand`** : base de commande pour les imports Excel.
- **`common.logger.Logger`, `InternalError`** : journalisation, collecte de messages et erreurs applicatives.
- **`common.router`** : `DatabaseOverrideRouter` et `database_override` pour le routage temporaire des lectures/écritures.
- **`common.auth.LdapBackend`** : authentification LDAP et synchronisation des comptes/groupes.
- **`common.operations`** : opérations de migration `CreateFunctionUnaccent` et `CreateIndexUnaccent` pour PostgreSQL.
- **`common.websocket`** : diffusion de notifications websocket optionnelle.
- **`common.views`** : inspection du cache réservée au superuser et génération de `scripts.js`.
- **Commandes Django** : `abort_sql` pour interrompre des sessions PostgreSQL ;
  `clear` pour supprimer les données d'une application (**destructif**).

## Template tags

Charger la bibliothèque avec `{% load tools %}`.

| Usage | Tags et filtres |
|---|---|
| Accès et conversion | `meta`, `get`, `parsedate`, `localize`, `json` / `encode`, `decode`. |
| Collections | `gather`, `split`, `sorted`, `reversed`, `drop`. |
| ORM et permissions | `query`, blocs `perm` et `anyperm`. |
| Rendu | `render` / `eval`, bloc `markdown`, bloc `obfuscate`. |
| Configuration serveur | `conf`, à réserver aux templates de confiance. |

## Configuration essentielle

Les options se définissent dans les settings Django ; leurs valeurs par défaut
sont réunies dans `common/settings.py`.

| Option | Usage / défaut |
|---|---|
| `ENABLE_API_PERMISSIONS` | Contrôles de permissions des modèles traversés ; `True`. |
| `API_MAX_PAGE_SIZE` | Plafond des pages hors administration ; `1000`. `all=true` est réservé aux administrateurs authentifiés. |
| `API_CACHE_PREFIX`, `API_CACHE_TIMEOUT` | Mémorisation des paramètres de recherche API. |
| `IGNORE_LOG`, `IGNORE_LOG_NO_USER`, `IGNORE_LOG_ENTITY_FIELDS` | Contrôle de l'historisation et de ses détails. |
| `IGNORE_GLOBAL` | Désactivation du référentiel global. |
| `CELERY_ENABLE` | Activation de Celery ; configurer avant le chargement des modèles. |
| `NOTIFY_CHANGES`, `NOTIFY_OPTIONS` | Activation et contenu des notifications ; désactivées par défaut. |
| `WEBSOCKET_ENABLED`, `WEBSOCKET_URL` | Transport websocket optionnel. |
| `SERVICE_USAGE` | Activation des compteurs/quotas ; `False`. |
| `SERVICE_USAGE_DEFAULT`, `SERVICE_USAGE_DATA` | Paramètres de quota par défaut et par nom de vue. |
| `SERVICE_USAGE_LIMIT_ONLY`, `SERVICE_USAGE_LOG_DATA` | Limitation aux quotas existants et journalisation des données d'appel. |
| `LDAP_ENABLE`, `LDAP_*` | Connexion, recherche et règles de synchronisation LDAP. |
| `PUBLIC_SETTINGS` | Liste explicite des settings publiés dans `scripts.js` ; ne jamais y placer de secret. |

Pour les quotas des vues Django, ajouter `common.middleware.ServiceUsageMiddleware`
après le middleware d'authentification. Pour les vues DRF hors Common, ajouter
`ServiceUsagePermission` **après les permissions d'accès**. Un appel admis consomme
son quota avant la vue ; une limite `0` interdit l'accès, `None` signifie illimité.

Les dépendances de base sont Django, DRF, python-dateutil et pytz.
Les intégrations disposent d'extras : `celery`, `excel`, `ldap`, `webhooks`,
`websocket`, `markdown`, `csv` et `postgresql`. Par exemple :

```bash
python -m pip install 'common-framework[excel,ldap,webhooks]'
```

`requirements.txt` décrit l'environnement de développement.

### Limites à connaître

- L'historisation Celery n'est **pas idempotente**, sans outbox ni garantie exactly-once.
  Les détails peuvent arriver après l'en-tête ; les opérations bulk/SQL ne déclenchent pas les signaux Django.
  Lors d'une mise à niveau, coordonner producteurs/workers et traiter les anciennes tâches en attente.
- Les garanties de concurrence des quotas nécessitent des verrous de lignes
  (pas SQLite) ; une transaction englobante telle qu'`ATOMIC_REQUESTS` peut aussi annuler leur comptage.
- `evaluate` et `execute` ne sont **pas une sandbox** ; `PickleField` et les templates dynamiques exigent des données/code de confiance.
- `process_file` ne prouve pas la fin d'un transfert et ne borne pas le volume décompressé ;
  `short_identifier` ne garantit pas l'unicité ; l'obfuscation n'est pas du chiffrement.

## Tests et développement

Les helpers publics de `common.tests` comprennent `BaseApiTestCase`,
`AuthenticatedBaseApiTestCase`, `create_api_test_class` et `create_auto_test_class`.
La suite technique est répartie par domaine dans `common/tests/`.

```bash
python -m pip install -r requirements.txt
python -m common.runtests
python -m common.runtests common.tests.api
python manage.py compilemessages
python -m coverage run -m common.runtests
python -m coverage html
python -m black --check common project manage.py setup.py
python -m flake8 common project manage.py setup.py --ignore=E501,W503,E203
python -m pip wheel --no-deps . --wheel-dir dist
```

Le lanceur utilise une base SQLite de test jetable. Les tests nécessitant
PostgreSQL, une seconde base ou Celery peuvent être ignorés selon l'environnement ;
l'échec attendu sur l'idempotence décrit un défaut reporté, pas un succès.

## Licence

MIT — voir [LICENSE.txt](LICENSE.txt).
