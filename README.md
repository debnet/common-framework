# Framework commun

Boîte à outils commune pour des développements autour de Django (https://www.djangoproject.com/) et 
Django REST Framework (http://www.django-rest-framework.org/).

## Django

### Entités

Une entité est un super-modèle Django offrant un nombre important de nouvelles fonctionnalités tels que l'historisation
des données, la réversion, les métadonnées, la sérialisation et la représentation des données sous forme de
dictionnaire.

Pour utiliser ces fonctionnalités, il est nécessaire de faire hériter chaque modèle de ``common.models.Entity`` qui
hérite lui-même de ``django.db.models.Model``.

```python
from common.models import Entity

class Personne(Entity):
    pass
```

Les modèles entités possèdent par défaut les champs suivants :
* ``creation_date`` : date de création de l'entité
* ``modification_date`` : date de dernière modification de l'entité
* ``current_user`` : dernier utilisateur à l'origine de la création/modification
* ``uuid`` : identifiant unique de l'entité

L'identifiant unique permet de retrouver n'importe quelle entité depuis la base de données car chacune est
enregistrée dans un référentiel global conjointement à son type. Il est donc possible de récupérer une entité ainsi :

```python
from common.models import Global

entity = Global.objects.from_uuid('4b9abebd-8157-4e49-bcae-1a7e063a9f86')
```

> Attention ! Les entités surchargent les méthodes de persistance par défaut de Django 
(``save()``, ``create()``, ``delete()``).
``update()`` garde cependant son comportement par défaut car il exécute directement la mise à jour en base de 
données, il sera donc impossible de détecter les changements si elle est utilisée.

Une version allégée d'entité est à disposition sans l'historisation ni le référentiel global, il suffit alors
d'hériter les modèles de ``common.models.CommonModel`` à la place de ``common.models.Entity``.

### Entités périssables

Une entité périssable possède une durée de validité dans le temps, elle possède les mêmes fonctionnalités que l'entité
mais possède en prime une date de début (``start_date``) et une date de fin potentielle (``end_date``).

Pour définir une entité périssable, il suffit de faire hériter le modèle de ``common.models.PerishableEntity`` au lieu
de ``common.models.Entity``. Il n'existe pas d'alternative simplifiée hors historisation des entités périssables.

Lorsqu'une entité périssable existante en base de données est sauvegardée, le mécanisme suivant s'exécute :
* si une date de fin est fournie, l'entité courante est modifiée et clôturée à cette date
* sans date de fin fournie, l'entité courante est clôturée à cette date et une autre entité avec les modifications est 
créée avec une date de début actualisée et sans date de fin programmée

Les entités périssables implémentent une fonction de récupération des données par rapport à une date donnée, si la date
n'est pas fournie la requête récupérera toutes les entités qui sont valides par rapport à la date et heure courante.

```python
adresses = Adresse.objects.filter(personne_id=1).select_valid(date='2016-08-01T00:00:00')
```

### Administration

Afin de garantir les fonctionnalités de l'historisation dans l'interface  d'administration, il est nécessaire de faire
hériter les classes d'administration de ``common.admin.EntityAdmin`` qui permet les fonctionnalités suivantes :

* Gère automatiquement l'utilisateur connecté pour les modifications
* Affiche automatiquement la date de création et de modification et fournit les filtres de recherche
* Gère les permissions d'affichage
* Affiche les métadonnées de l'entité

Pour les entités périssables, il convient d'utiliser ``common.admin.PerishableEntityAdmin`` à la place de
``common.admin.EntityAdmin``.

Pour les inlines, des équivalents pour les entités sont également à disposition : 
``common.admin.EntityTabularInline`` et ``common.admin.EntityStackedInline``.

Une version allégée existe pour ``common.models.CommonModel`` qui est ``common.admin.CommonAdmin`` qui implémente
l'ensemble des fonctionnalités en dehors de l'historisation.

### Historisation

Toute altération au niveau des données d'un modèle entité est détectée et enregistrée dans un historique,
cela permet un suivi des modifications par utilisation ainsi que la possibilité de revenir à un état antérieur pour
l'entité ou un champ spécifique de l'entité.

A chaque modification, un historique d'entité (``common.models.History``) est sauvegardé pour l'entité et un élément 
par champ modifié dans l'historique (``common.models.HistoryField``) uniquement s'il s'agit d'une modification.

* Les relations de type many-to-many sont traités séparément à l'historisation.
* Si Celery est installé (http://www.celeryproject.org/), l'historisation est exécutée de manière asynchrone.
* L'utilisateur à l'origine de la modification est conservé dans l'historique.
* Il est possible d'ajouter un message et/ou modifier l'utilisateur à l'historique via le code.

```python
personne = Personne(nom='Marc', age=30)
personne.save(_current_user=utilisateur, _reason="Message d'information")
```

Il est possible sur un historique d'entité ou un historique de champ de demander une restauration des données de
l'historique ciblé sur l'entité concernée grâce à la méthode ```restore()```, il est possible de lui passer
également un utilisateur et un message d'information.

La restauration provoque elle-même un historique spécifique qui permet de revenir en arrière si nécessaire.

```python
history.restore(current_user=utilisateur, reason="Message d'information", rollback=False)
# rollback permet de regénérer complètement l'entité si elle a été supprimée
```

### Métadonnées

Les métadonnées permettent d'ajouter des données tierces sur une entité, ces données peuvent être structurées comme
l'utilisateur le souhaitera et seront stockées sous forme de JSON dans la base de données. Chaque entité possède des
méthodes permettant d'accéder aux métadonnées de l'entité.

Les données stockées dans les métadonnées peuvent être de n'importe quel type Python à condition que ce dernier ainsi
que les données qu'il contient éventuellement soient sérialisables (list, dict, int, float, etc...).

```python
personne.set_metadata('cle', 'valeur')
personne.get_metadata('cle')
>>> 'valeur'
personne.del_metadata('cle')
personne.get_metadata('cle')
>>> None
```

Les métadonnées d'une entité peuvent être directement utilisées dans des requêtes même si ce n'est pas recommandé pour
des raisons de performances (en fonction du volume de données).
Elles sont représentées sous forme d'un modèle Django ``common.models.MetaData``.

```python
Personne.objects.filter(metadata__key='cle', metadata__value='valeur').first()
MetaData.objects.search(key='cle', value='valeur', type=Personne)
```

### Sérialisation

Chaque entité ou requête concernant une entité peut être sérialisée en utilisant la méthode 
``serialize(format='json')`` dans l'un des formats suivants:
* JSON
* XML
* YAML

```python
personne = Personne.objects.first()
resultat = personne.serialize()
resultat
>>> [json] Personne (1)
resultat.data
>>> '[{"nom": "Marc", "age": 30}]'
personne = resultat.deserialize()
personne
>>> Personne: Marc (30 ans)
```

```python
personnes = Personne.objects.all()
resultat = personnes.serialize()
resultat
>>> [json] Personne (2)
resultat.data
>>> '[{"nom": "Marc", "age": 30}, {"nom": "Eric", "age": 40}]'
personnes = resultat.deserialize()
personnes
>>> [Personne: Marc (30 ans), Personne: Eric (40 ans)]
```

### Représentation en dictionnaire

Une requête ou une entité peut être représentée par un dictionnaire Python avec la méthode ``to_dict()``.

```python
personne.to_dict()
>>> {"nom": "Marc", "age": 30}
Personne.objects.all().to_dict()
>>> [{"nom": "Marc", "age": 30}, {"nom": "Eric", "age": 40}]
```

``to_dict()`` prend plusieurs arguments optionnels en paramètres, la méthode est documentée dans le code.

### Type d'entité

Django conserve systématiquement le type des différents modèles en base de données, les entités possèdent un moyen
simple pour récupérer ce type sur chaque entité de manière performante et unique.

```python
Personne.get_model_type()
>>> <ContentType: Personne>
personne.model_type
>>> <ContentType: Personne>
```

### WebHooks

Un webhook est un callback HTTP qui transmet des données à un serveur externe en fonction d'une action 
réalisée sur l'application. Le récepteur doit être en mesure de comprendre le message qui est transmis.
Il est possible de configurer un webhook qui réagit à un ou plusieurs actions sur une ou plusieurs entités.

Les actions couvertes sont les suivantes :
* Création
* Modification
* Suppression
* Relations de type many-to-many

Un webhook peut être configuré pour utiliser une authentification sur le serveur externe si nécessaire.

Par défaut, le webhook exécute la méthode ``to_dict()`` sur l'instance concerné avec les paramètres définis dans
``NOTIFY_OPTIONS``, mais il est possible de changer ce comportement en définissant une méthode ``get_webhook_data`` au
niveau du modèle.

Les notifications des changements par webhook est désactivée par défaut et peut être activé via ``NOTIFY_CHANGES``.

### Usage de service

L'usage des services permet de compter le nombre de fois où une URL est appelée dans l'application par un même
utilisateur et même de limiter le nombre d'usages.

La fonctionnalité est désactivée par défaut et peut être activée via ``SERVICE_USAGE``. 
Il est également nécessaire d'ajouter ``'common.middleware.ServiceUsageMiddleware'`` dans ``MIDDLEWARE_CLASSES``.

Configuration :

* ``SERVICE_USAGE`` (``False`` par défaut) : active ou non la surveillance
* ``SERVICE_USAGE_LOG_DATA`` (``False`` par défaut) : suit également les données transmisses par les services
* ``SERVICE_USAGE_LIMIT_ONLY`` (``False`` par défaut) : utilise uniquement le système de restriction des services

### Métadonnées utilisateurs & groupes

De la même manière que sur les entités, les utilisateurs et les groupes ont la possibilité de conserver de 
l'information contextuelle sous forme de métadonnée, cependant le fonctionnement diffère car ces modèles peuvent être
substitués dans le développement.

Il s'agit alors d'une relation de type one-to-one systématiquement présent à la création d'un nouvel utilisateur ou
d'un nouveau groupe et possédant un champ de type JSON pour contenir ces données.

## Utilitaires

Un grand nombre de fonctions, décorateurs et classes utilitaires sont à disposition des développeurs pour accélérer la
production de nouvelles fonctionnalités. Ces utilitaires sont regroupés dans ``common.utils`` pour tout ce qui est
relatif à Django et dans ``common.api.utils`` pour tout ce qui est relatif à Django REST Framework.

* ``singleton`` : décorateur permettant de transformer une classe en singleton
* ``get_current_app`` : permet de récupérer l'application Celery actuelle ou un mock si Celery n'est pas installé
* ``parsedate`` : permet d'évaluer une date dans n'importe quel format
* ``timeit`` : décorateur permettant de calculer le temps d'exécution d'une fonction
* ``synchronized`` : décorateur permettant de rendre thread-safe l'exécution d'une fonction
* ``temporary_upload`` : décorateur permettant à une vue de supporter l'import d'un fichier de manière temporaire
* ``download_file`` : décorateur permettant à une vue de supporter le téléchargement d'un fichier
* ``render_to`` : décorateur permettant de simplifier l'écriture d'une vue avec template
* ``ajax_request`` : décorateur permettant à une vue de se comporter comme une api_view
* ``evaluate`` : permet d'évaluer une expression Python de manière plus sûre
* ``execute`` : permet d'exécuter du code Python de manière plus sûre
* ``patch_settings`` : context manager permettant d'altérer la configuration le temps de l'exécution
* ``recursive_dict_product`` : permet de faire un produit cartésien des données d'un dictionnaire
* ``get_choices_fields`` : permet de récupérer les choix des modèles d'une ou plusieurs applications
* ``get_prefetchs`` : permet de récupérer toutes les relations inversées d'un modèle
* ``get_related`` : permet de récupérer toutes les relations ascendantes d'un modèle
* ``prefetch_generics`` : permet de récupérer les relations génériques d'un modèle
* ``str_to_bool`` : permet de convertir une chaîne de caractères quelconque en booléen
* ``decimal`` : permet de convertir un élément quelconque en nombre décimal
* ``decimal_to_str`` : permet de convertir un nombre décimal en chaîne de caractères
* ``recursive_get_urls`` : permet de récupérer toutes les URLs d'un module
* ``idict`` : dictionnaire donc les clés sont toujours converties dans un format uniforme
* ``sort_dict`` : permet de trier un dictionnaire par ses clés
* ``merge_dict`` : permet de fusionner un ou plusieurs dictionnaires imbriqués sur un autre
* ``null`` : objet nul absolu retournant toujours une valeur nulle sans erreur
* ``to_tuple`` : permet de convertir un dictionnaire en un tuple
* ``to_object`` : permet de convertir un dictionnaire en un objet Python
* ``get_size`` : permet de récupérer la taille en mémoire d'un objet Python quelconque
* ``file_is_text`` : permet de vérifier qu'un fichier est au format texte
* ``process_file`` : permet de s'assurer qu'un fichier est bien complet et décompresse les éventuelles archives
* ``base64_encode`` : permet d'encoder une chaîne de caractères en base 64
* ``base64_decode`` : permet de décoder une chaîne de caractères en base 64
* ``short_identifier`` : permet de générer un identifiant "unique" court
* ``json_encode`` : permet de sérialiser un objet Python en JSON
* ``json_decode`` : permet de désérialiser une chaîne de caractères JSON en objet Python
* ``get_current_user`` : permet de récupérer l'utilisateur actuellement connecté dans la pile d'exécution
* ``get_pk_field`` : permet de récupérer le champ de clé primaire d'un modèle en héritage concret
* ``collect_deleted_data`` : permet de récupérer les impacts potentiels d'une suppression d'entité
* ``send_mail`` : permet d'envoyer un email
* ``merge_validation_errors`` : permet de fusionner plusieurs exceptions de validation en une seule
* ``get_all_models`` : récupère tous les modèles enregistrés dans les applications
* ``get_all_permissions`` : récupère toutes les permissions existantes dans les applications
* ``get_models_from_queryset`` : recupère tous les modèles qui ont été traversés par une requête
* ``get_model_permissions`` : récupère toutes les permissions liées à un modèle et à un utilisateur
* ``get_client_ip`` : récupère l'adresse IP du client depuis une requête Django
* ``hash_file`` : calcule la somme de contrôle d'un fichier

##### Autres (``common.admin``)

* ``create_admin`` : permet de créer automatiquement les classes d'administration d'un modèle

### Modèles

Les differents utilitaires de modèles sont définis dans ``common.models``.

* ``CustomGenericForeignKey`` : champ générique qui ne vide pas les propriétés de la clé si l'instance n'existe pas
* ``CustomGenericRelation`` : relation d'une clé étrangère générique qui force la conversion de l'identifiant en texte
* ``MetaData`` : modèle des métadonnées associées aux entités
* ``CommonModel`` : modèle abstrait commun permettant d'accéder aux multiples utilitaires décrits précédemment
* ``Entity`` : modèle commun avec historisation des modifications
* ``PerishableEntity`` : même chose qu'``Entity`` mais se duplicant en cas de modification
* ``History`` : modèle d'historique d'une entité
* ``HistoryField`` : modèle d'historique d'un champ d'une entité
* ``Global`` : modèle point d'entrée global des entités
* ``Webhook`` : modèle de configuration d'un webhook
* ``ServiceUsage`` : modèle d'un historique d'accès à une ressource du site (avec ou sans limitation)
* ``from_dict`` : construit une instance d'un modèle à partir d'un dictionnaire
* ``to_dict`` : appel générique transformant une instance de modèle en dictionnaire
* ``model_to_dict`` : transforme récursivement une instance de modèle en dictionnaire

### Champs de modèles

Les champs de modèle sont définis dans ``common.fields``.

* ``CustomDecimalField`` : champ décimal pour éviter la représentation scientifique des nombres
* ``PickleField`` : champ binaire pour contenir de la donnée Python brute
* ``JsonField`` : champ pour représenter des données JSON (compatible avec les autres SGBD)

### Formulaires

Les utilitaires autour des formulaires sont définis dans ``common.forms``. Chaque classe utilitaire pour les
formulaires possède une interface de base, préfixée généralement ``Base`` pour d'autres implémentations.

* ``CommonForm`` : classe de base pour les formulaires avec gestion des historiques
* ``CommonFormSet`` : classe de base pour les ensembles de formulaires avec gestion des historiques
* ``CommonModelForm`` : classe de base pour représenter les formulaires issus d'entités
* ``CommonModelFormSet`` : classe de base pour représenter les ensembles de formulaires issus d'entités
* ``JsonField`` : champ de formulaire pour représenter les données JSON
* ``BaseFilterForm`` : classe de base pour faciliter la création de formulaires de recherche
* ``get_model_form`` : fonction permettant de créer un formulaire d'entité avec des imbrications

# Django REST Framework

## Usage

La boîte à outils vous permet de générer rapidement des APIs RESTful pour vos modèles de base de données en leur
fournissant au passage le moyen de faire des requêtes plus évoluées via l'URL. Cette génération s'adresse
principalement aux développeurs qui ont besoin d'accéder à toute la richesse de leurs modèles et de l'ORM Django
via les APIs le plus rapidement possible sans avoir à configurer finement chaque ressource.

### Configuration rapide

Dans votre application Django, créez un nouveau module et ajoutez ces quelques lignes :

```python
from common.api.utils import create_api
from myapp.models import ModelA, ModelB

namespace = "myapp-api"
app_name = "myapp-api"
router, all_serializers, all_viewsets = create_api(ModelA, ModelB)
urlpatterns = [
    # Vos URLs d'API personnalisées    
] + router.urls
urls = (urlpatterns, namespace, app_name)
```

Cela a pour conséquence de créer automatiquement les APIs pour les modèles que vous souhaitez ainsi que la table de
routage nécessaire pour accéder à ces ressources.

De nombreuses options de configuration existent pour vos APIs, vous pouvez par exemple définir précisément le
serialiseur pour chaque modèle, configurer la récupération automatique des entités liées par clé étrangère (related) ou
des relations de type plusieurs-à-plusieurs (prefetch) ou personnaliser les requêtes.

Pour configurer globalement et de manière homogène vos modèles, modifiez les paramètres dans ``common.api.base``.

### Guide d'utilisation rapide



## Utilitaires

* ``to_model_serializer`` : décorateur permettant de convertir un sérialiseur classique en sérialiseur de modèle
* ``to_model_viewset`` : décorateur permettant d'associer un modèle et à un sérialiseur à une vue
* ``create_model_serializer_and_viewset`` : permet de créer en une fois le sérialiseur et la vue pour un modèle
* ``perishable_view`` : permet de gérer les données périssables d'une vue à travers l'URL
* ``api_view_with_serializer`` : décorateur permettant de créer une vue assujettie à un sérialiseur
* ``create_model_serializer`` : permet de créer un sérialiseur de modèle
* ``auto_view`` : décorateur permettant de créer une vue à partir d'un QuerySet
* ``api_paginate`` : permet d'ajouter une pagination sur le résultat d'une requête pour une vue
* ``create_api`` : permet de créer les APIs standards (RESTful) pour un ou plusieurs modèles
* ``disable_relation_fields`` : permet de désactiver les listes déroulantes pour les relations des APIs

##### Sérialiseurs (``common.api.serializers``)

* ``CommonModelSerializer`` : sérialiseur commun pour représenter les entités
* ``GenericFormSerializer`` : sérialiseur permettant l'imbrication d'entités pour les formulaires

##### Champs (``common.api.fields``)

* ``JsonField`` : champ permettant la gestion des données JSON
* ``AsymetricRelatedField`` : champ permettant l'affichage de l'ensemble des données des entités de clés étrangères
mais accepte néanmoins un simple identifiant à la création/modification
* ``CustomHyperlinkedField`` : champ permettant de gérer les liens vers d'autres APIs en permettant de croiser
les différents namespaces (utilisé par défaut par les utilitaires)

##### Pagination (``common.api.pagination``)

* ``CustomPageNumberPagination`` : pagination améliorée pour les APIs 
(doit être défini dans ``DEFAULT_PAGINATION_CLASS`` de ``REST_FRAMEWORK``)

##### Rendu (``common.api.renderers``)

* ``CustomCSVRenderer`` : rendu CSV amélioré avec téléchargement (uniquement si django-rest-framework-csv est installé,
doit être défini dans ``DEFAULT_RENDERER_CLASSES`` de ``REST_FRAMEWORK``)

##### Tests (``common.tests``)

* ``BaseApiTestCase`` : classe de base pour les tests unitaires des APIs
* ``AuthenticatedBaseApiTestCase`` : classe de base pour les tests unitaires des APIs avec authentification
* ``create_api_test_class`` : fonction pour générer tous les tests d'une API standard (RESTful)
