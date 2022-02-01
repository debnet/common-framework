# coding: utf-8
import inspect
import sys
from datetime import timedelta
from functools import wraps

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase as Test
from django.utils.text import slugify
from django.utils.timezone import now
from model_bakery.recipe import Recipe
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIRequestFactory, APITestCase

from common.api.utils import create_model_serializer
from common.models import CommonModel, Entity, PerishableEntity
from common.utils import get_pk_field, json_decode, json_encode

# Modèle utilisateur courant
User = get_user_model()


class BaseApiTestCase(APITestCase):
    """
    Classe de test api de base gérant la création & suppression du user_admin
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_admin = User.objects.filter(username="admin").first()
        if not cls.user_admin:
            cls.user_admin = User.objects.create_superuser("admin", "admin@domain.local", "admin")

    @classmethod
    def tearDownClass(cls):
        cls.user_admin.delete()
        super().tearDownClass()

    def _test_access(self, func, *args, **kwargs):
        response = func(*args, **kwargs)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.client.force_authenticate(self.user_admin)
        response = func(*args, **kwargs)
        self.assertNotIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        return response


class AuthenticatedBaseApiTestCase(BaseApiTestCase):
    """
    Classe de test api de base avec authentification du user_admin en setUp
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user_admin)


def raise_exception(error_type, code):
    """
    Décorateur permettant de tester les levées d'exceptions
    :param error_type: Type d'exception
    :param code: Code de l'exception à retrouver
    :return: Méthode décorée
    """

    def decorated(method):
        @wraps(method)
        def wrapper(instance, *args, **kwargs):
            with instance.assertRaises(error_type) as cm:
                method(instance, *args, **kwargs)
            exceptions = cm.exception.error_list
            instance.assertEqual(1, len(exceptions))
            instance.assertEqual(code, exceptions[0].code)

        return wrapper

    return decorated


def create_api_test_class(
    model,
    serializer=None,
    data=None,
    module=True,
    namespace=None,
    test_list=True,
    test_get=True,
    test_post=True,
    test_put=True,
    test_delete=True,
    test_options=True,
    test_order_by=True,
    test_filter=True,
    test_fields=True,
    test_metadata=True,
    test_simple=True,
    test_silent=True,
    test_annotate=True,
    test_aggregate=True,
):
    """
    Permet d'obtenir la classe de test du modèle avec les méthodes de tests standard de l'api
    :param model: Modèle
    :param serializer: Serializer spécifique à utiliser, si absent un serializer associé au modèle sera généré
    :param data: liste de dictionnaires contenant les valeurs des attributs à positionner obligatoirement pour ce modèle
    :param module: Ajoute la classe de test dans le module appelant
    :param namespace: Namespace des URLs d'API
    :param test_list: Test de la liste
    :param test_get: Test du GET
    :param test_post: Test du POST
    :param test_put: Test du PUT
    :param test_delete: Test du DELETE
    :param test_options: Test du OPTIONS
    :param test_order_by: Test des tris
    :param test_filter: Test des filtres
    :param test_fields: Test des restrictions de champs
    :param test_metadata: Test des metadata
    :param test_simple: Test des réquêtes simplifiées
    :param test_silent: Test de la remontée d'erreur silencieuse
    :param test_annotate: Test des annotations
    :param test_aggregate: Test des aggregations
    :return: Classe de test
    """
    app_label = model._meta.app_label
    object_name = model._meta.object_name
    model_name = model._meta.model_name
    pk_field = get_pk_field(model).name

    class_name = "{}{}AutoTest".format(app_label.capitalize(), object_name)
    test_class = type(class_name, (APITestCase,), {})
    test_class.recipes_data = data

    # Modification du module de la classe à partir de l'appelant
    if module:
        module_name = inspect.getmodule(inspect.stack()[1][0]).__name__
        setattr(sys.modules[module_name], class_name, test_class)
        test_class.__module__ = module_name

    def _setUpClass(cls):
        """
        Ajout de l'admin user et des schémas d'url pour les actions de detail & list
        """
        super(APITestCase, cls).setUpClass()
        cls.user_admin = User.objects.filter(username="admin").first()
        if not cls.user_admin:
            cls.user_admin = User.objects.create_superuser("admin", "admin@test.fr", "admin")
        if not namespace:
            cls.url_list_api = "{}-list".format(model_name)
            cls.url_detail_api = "{}-detail".format(model_name)
        else:
            cls.url_list_api = "{}:{}-list".format(namespace, model_name)
            cls.url_detail_api = "{}:{}-detail".format(namespace, model_name)
        cls.serializer = serializer or create_model_serializer(model, hyperlinked=False)

    test_class.setUpClass = classmethod(_setUpClass)

    def _tearDownClass(cls):
        """
        Suppression de l'admin
        """
        cls.user_admin.delete()
        super(APITestCase, cls).tearDownClass()

    test_class.tearDownClass = classmethod(_tearDownClass)

    def _get_recipes(self):
        """
        Génération des recipes
        """
        recipes = []
        if self.recipes_data:
            for recipe_data in self.recipes_data:
                attrs = recipe_data.copy()
                for key, value in attrs.items():
                    if callable(value):
                        attrs[key] = value()
                recipes.append(Recipe(model, **attrs))
        else:
            recipes.append(Recipe(model))
        return recipes

    test_class.get_recipes = _get_recipes
    test_class.recipes = property(_get_recipes)

    if test_list:

        def _test_api_list(self):
            """
            Méthode de test de la liste des éléments
            """
            items_count = model.objects.count()
            for item in self.recipes:
                item.make()
            url = reverse(self.url_list_api)
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data["count"], items_count + len(self.recipes))

        test_class.test_api_list = _test_api_list

    if test_get:

        def _test_api_detail(self):
            """
            Méthode de test du détail d'un élément
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=(item.pk,))
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data[pk_field], item.pk)

        test_class.test_api_detail = _test_api_detail

    if test_post:

        def _test_api_post(self):
            """
            Méthode de test de création d'un élément
            """
            is_perishable = issubclass(model, PerishableEntity)
            # Permet d'eviter l'enregistrement d'une perissable exactement à la même date
            if is_perishable:
                start_date = now() - timedelta(days=1)
                item = self.recipes[0].make(make_m2m=True, start_date=start_date)
            else:
                item = self.recipes[0].make(make_m2m=True)
            request = APIRequestFactory().request()
            data_to_post = self.serializer(item, context=dict(request=request)).data
            if is_perishable:
                data_to_post["start_date"] = None
            item.delete(keep_parents=False, **(dict(_force_default=True) if issubclass(model, Entity) else {}))
            url = reverse(self.url_list_api)
            response = self.client.post(url, data_to_post)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.post(url, data_to_post)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
            self.assertIsNotNone(response.data[pk_field])
            if is_perishable:
                self.assertIsNotNone(response.data["start_date"])

        test_class.test_api_post = _test_api_post

    if test_put:

        def _test_api_put(self):
            """
            Méthode de test de modification d'un élément
            """
            backery_make_args = dict(make_m2m=True)
            if issubclass(model, PerishableEntity):
                backery_make_args["start_date"] = now()
            item = self.recipes[0].make(**backery_make_args)
            url = reverse(self.url_detail_api, args=(item.pk,))
            request = APIRequestFactory().request()
            data_to_put = self.serializer(item, context=dict(request=request)).data
            response = self.client.put(url, data_to_put)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.put(url, data_to_put)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            # Dans le cas des PerishableEntity, on vérifie que l'entité modifiée est en fait "périmée" et
            # qu'une nouvelle entité est créée à la bonne date (pour l'historisation)
            if issubclass(model, PerishableEntity):
                # ancienne entité périmée => possède une date de fin
                old_item = model.objects.get(pk=item.pk)
                self.assertIsNotNone(old_item.end_date)
                # L'entité retournée doit être une nouvelle entité sans date de fin
                self.assertNotEqual(response.data[pk_field], item.pk)
                self.assertIsNone(response.data["end_date"])
            else:
                self.assertEqual(response.data[pk_field], item.pk)

        test_class.test_api_put = _test_api_put

    if test_delete:

        def _test_api_delete(self):
            """
            Méthode de test de suppression d'un élément
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=(item.pk,))
            response = self.client.delete(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.delete(url)
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.data)
            get_item = model.objects.filter(pk=item.pk).first()
            if issubclass(model, PerishableEntity):
                self.assertIsNotNone(get_item.end_date)
            else:
                self.assertIsNone(get_item)

        test_class.test_api_delete = _test_api_delete

    if test_options:

        def _test_api_options(self):
            """
            Methode de test de métadonnées
            """
            url = reverse(self.url_list_api)
            response = self.client.options(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.options(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        test_class.test_api_options = _test_api_options

    if test_order_by:

        def _test_api_order_by(self):
            """
            Méthode de test de tri
            """
            if len(self.recipes) > 1:
                for item in self.recipes:
                    item.make()
            else:
                self.recipes[0].make(_quantity=2)
            url = reverse(self.url_list_api) + "?order_by=-" + pk_field
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            item1, item2 = response.data["results"][0], response.data["results"][1]
            value1, value2 = item1[pk_field], item2[pk_field]
            if isinstance(value1, str) or isinstance(value1, str):
                value1, value2 = value1.lower(), value2.lower()
            self.assertGreater(value1, value2)
            url = reverse(self.url_list_api) + "?order_by=" + pk_field
            response = self.client.get(url)
            item1, item2 = response.data["results"][0], response.data["results"][1]
            value1, value2 = item1[pk_field], item2[pk_field]
            if isinstance(value1, str) or isinstance(value1, str):
                value1, value2 = value1.lower(), value2.lower()
            self.assertLess(value1, value2)

        test_class.test_api_order_by = _test_api_order_by

    if test_filter:

        def _test_api_filter_list(self):
            """
            Méthode de test du filtre lors d'un get list
            """
            items_ids = []
            for item in self.recipes:
                instance = item.make()
                items_ids.append(str(instance.pk))
            # Test avec résultat
            url = reverse(self.url_list_api) + "?{}__in={}".format(pk_field, ",".join(items_ids[:2]))
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data["count"], min(len(items_ids), 2))
            self.assertTrue(all(i.get(pk_field, None) in items_ids for i in response.get("results", [])))
            options = response.data.get("options", {})
            self.assertTrue(options.get("filters", False))
            # Test sans résultat
            url = reverse(self.url_list_api) + "?" + pk_field + "=0"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data["count"], min(len(items_ids), 0))

        test_class.test_api_filter_list = _test_api_filter_list

        def _test_api_filter_get(self):
            """
            Méthode de test du filtre lors d'un get unitaire
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=(item.pk,)) + "?{}={}".format(pk_field, item.pk)
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data.get(pk_field, None), item.pk)
            # Test sans résultat
            url = reverse(self.url_detail_api, args=(item.pk,)) + "?" + pk_field + "=0"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        test_class.test_api_filter_get = _test_api_filter_get

    if test_fields:

        def _test_api_fields(self):
            """
            Méthode de test des restrictions de champ
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=(item.pk,)) + "?fields={}".format(pk_field)
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data.get(pk_field, None), item.pk)
            self.assertEqual(len(response.data), 1)

        test_class.test_api_fields = _test_api_fields

    if test_metadata and issubclass(model, CommonModel):

        def _test_api_metadata(self):
            """
            Méthode de test des metadata
            """
            item = self.recipes[0].make()
            item.set_metadata("test_key", "test_value")
            # Test sans metadata
            url = reverse(self.url_detail_api, args=(item.pk,))
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertIsNone(response.data.get("metadata"))
            # Test avec metadata
            url = reverse(self.url_detail_api, args=(item.pk,)) + "?meta=1"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            metadata = response.data.get("metadata", {})
            self.assertEqual(len(metadata), 1)
            key, value = next(iter(metadata.items()))
            self.assertEqual(key, "test_key")
            self.assertEqual(value, "test_value")

        test_class.test_api_metadata = _test_api_metadata

    if test_simple:
        from django.db.models import ManyToOneRel
        from django.db.models.fields.related import ForeignKey

        def _test_api_simple(self):
            """
            Méthode de test du simple
            """
            recipe, *junk = self.recipes
            item = recipe.make()
            # Récupération submodels sans le simple
            self.client.force_authenticate(self.user_admin)
            url = reverse(self.url_detail_api, args=(item.pk,))
            response = self.client.get(url)
            list_submodels = []
            unique_submodels = []
            for field in model._meta.get_fields():
                field_name = field.get_accessor_name() if isinstance(field, ManyToOneRel) else field.name
                if field_name in response.data:
                    field_data = response.data[field_name]
                    if (field.one_to_many or field.many_to_many) and isinstance(field_data, list):
                        list_submodels.append(field_name)
                    elif (field.one_to_one or isinstance(field, ForeignKey)) and isinstance(field_name, dict):
                        unique_submodels.append(field_name)
            # On vérifie qu'ils ne soient plus remontés avec le simple
            url = reverse(self.url_detail_api, args=(item.pk,)) + "?simple=1"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(all(field not in response.data for field in list_submodels))
            self.assertTrue(
                all(
                    field not in response.data or not isinstance(response.data.get(field, {}), dict)
                    for field in list_submodels
                )
            )

        test_class.test_api_simple = _test_api_simple

    if test_silent:

        def _test_api_silent(self):
            """
            Méthode de test du silent
            """
            items_count = model.objects.count()
            for item in self.recipes:
                item.make()
            # Test sans le silent
            self.client.force_authenticate(self.user_admin)
            url = reverse(self.url_list_api) + "?test_field_does_not_exist=test"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            # Test avec le silent
            url = reverse(self.url_list_api) + "?test_field_does_not_exist=test&silent=1"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data["count"], items_count + len(self.recipes))
            options = response.data.get("options", {})
            self.assertFalse(options.get("filters", True))

        test_class.test_api_silent = _test_api_silent

    if test_annotate:

        def _test_api_annotate(self):
            """
            Méthode de test d'une annotation sur un champ
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=(item.pk,)) + "?lpad={}:str;10;'_'|test_annotate".format(pk_field)
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data.get("test_annotate"), str(item.pk).rjust(10, "_")[:10])

        test_class.test_api_annotate = _test_api_annotate

    if test_aggregate:

        def _test_api_aggregate(self):
            """
            Méthode de test d'une aggregation de données
            """
            items_count = model.objects.count()
            self.recipes[0].make()
            url = reverse(self.url_list_api) + "?count={}|test_count".format(pk_field)
            response = self.client.get(url)
            self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data.get("test_count"), items_count + 1)

        test_class.test_api_aggregate = _test_api_aggregate

    return test_class


OPERATORS = {
    "==": Test.assertEqual,
    "!=": Test.assertNotEqual,
    "~=": Test.assertAlmostEqual,
    "<=": Test.assertLessEqual,
    "<": Test.assertLess,
    ">=": Test.assertGreaterEqual,
    ">": Test.assertGreater,
    "@>": Test.assertContains,
    "<@": Test.assertIn,
    "!>": Test.assertNotContains,
    "<!": Test.assertNotIn,
    "#": Test.assertCountEqual,
}


def create_auto_test_class(fixture, callback):
    """
    Crée un test automatisé à partir d'une fixture complète
    :param fixture: Chemin vers la fixture
    :param callback: Fonction retournant les résultats à vérifier
    :return: Classe de test
    """
    import decimal
    import os
    import tempfile

    # Classe de test transactionnelle supprimant la fixture à la fin
    class AutoTest(Test):
        @classmethod
        def tearDownClass(cls):
            for fixture in cls.fixtures:
                os.remove(fixture)
            super().tearDownClass()

    # Récupération du nom depuis le fichier
    name, ext = os.path.splitext(os.path.basename(fixture))
    name = "".join(x.capitalize() or "_" for x in name.split("_")) + "TestCase"
    # Lecture des données
    with open(fixture, "r") as file:
        data = json_decode(file.read())
    # Création d'une fixture temporaire à partir des modèles
    models = data.pop("models", {})
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".json") as temp_file:
        temp_file.write(json_encode(models))
        temp_file.flush()
    # Création de la classe de test
    test_class = type(name, (AutoTest,), dict(data=data, fixtures=[temp_file.name]))
    # Création des méthodes de test
    tests = data.pop("tests")
    for index, test in enumerate(tests, start=1):

        def test_method(self, test=test):
            data = callback(test)
            with self.subTest():
                for result in test.get("results", []):
                    fields = result.get("fields", [])
                    value = result.get("value", None)
                    current = data
                    for field in fields:
                        current = current[field]
                    # Conversion explicite en décimal
                    if isinstance(current, decimal.Decimal):
                        value = decimal.Decimal(str(value))
                    operator = OPERATORS.get(result.get("operator", "=="))
                    operator(self, current, value)

        test_name = "test_{}".format(slugify(test.get("name", index)).replace("-", "_"))
        setattr(test_class, test_name, test_method)
    return test_class
