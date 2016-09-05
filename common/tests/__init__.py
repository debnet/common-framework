# coding: utf-8
from datetime import timedelta
from functools import wraps

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase as Test
from django.utils.text import slugify
from django.utils.timezone import now
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from model_mommy.recipe import Recipe
from common.api.utils import create_model_serializer
from common.models import Entity, PerishableEntity
from common.utils import json_decode, json_encode


# Modèle utilisateur courant
User = get_user_model()


class BaseApiTestCase(APITestCase):
    """
    Classe de test api de base gérant la création & suppression du user_admin
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_admin = User.objects.filter(username='admin').first()
        if not cls.user_admin:
            cls.user_admin = User.objects.create_superuser('admin', 'admin@sa-cim.fr', 'admin')

    @classmethod
    def tearDownClass(cls):
        cls.user_admin.delete()
        super().tearDownClass()

    def _test_access(self, func, *args, **kwargs):
        response = func(*args, **kwargs)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        self.client.force_authenticate(self.user_admin)
        response = func(*args, **kwargs)
        self.assertNotIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
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
        model, serializer=None, data=None,
        test_list=True, test_get=True, test_post=True, test_put=True,
        test_delete=True, test_options=True, test_order_by=True):
    """
    Permet d'obtenir la classe de test du modèle avec les méthodes de tests standard de l'api
    :param model: Modèle
    :param serializer: Serializer spécifique à utiliser, si absent un serializer associé au modèle sera généré
    :param data: liste de dictionnaires contenant les valeurs des attributs à positionner obligatoirement pour ce modèle
    :param test_list: Test de la liste
    :param test_get: Test du GET
    :param test_post: Test du POST
    :param test_put: Test du PUT
    :param test_delete: Test du DELETE
    :param test_options: Test du OPTIONS
    :param test_order_by: Test du tri
    :return: Classe de test
    """
    app_label = model._meta.app_label
    object_name = model._meta.object_name
    model_name = model._meta.model_name

    test_class = type('{}{}AutoTest'.format(app_label.capitalize(), object_name), (APITestCase, ), {})
    test_class.recipes_data = data

    def _setUpClass(cls):
        """
        Ajout de l'admin user et des schémas d'url pour les actions de detail & list
        """
        super(APITestCase, cls).setUpClass()
        cls.user_admin = User.objects.filter(username='admin').first()
        if not cls.user_admin:
            cls.user_admin = User.objects.create_superuser('admin', 'admin@sa-cim.fr', 'admin')
        cls.url_list_api = '{}-api:{}-list'.format(app_label, model_name)
        cls.url_detail_api = '{}-api:{}-detail'.format(app_label, model_name)
        cls.serializer = serializer or create_model_serializer(model)
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
            recipes = self.recipes
            nb_recipes = len(recipes)
            for item in recipes:
                item.make()
            url = reverse(self.url_list_api)
            response = self.client.get(url)
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data['count'], items_count + nb_recipes)
        test_class.test_api_list = _test_api_list

    if test_get:
        def _test_api_detail(self):
            """
            Méthode de test du détail d'un élément
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=[item.id])
            response = self.client.get(url)
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data['id'], item.id)
        test_class.test_api_detail = _test_api_detail

    if test_post:
        def _test_api_post(self):
            """
            Méthode de test de création d'un élément
            """
            perissable = issubclass(model, PerishableEntity)
            # Permet d'eviter l'enregistrement d'une perissable exactement à la même date
            if perissable:
                start_date = now() - timedelta(days=1)
                item = self.recipes[0].make(make_m2m=True, start_date=start_date)
            else:
                item = self.recipes[0].make(make_m2m=True)

            kwargs = dict(force_default=True) if issubclass(model, Entity) else {}
            item.delete(**kwargs)
            data_to_post = self.serializer(item).data
            if perissable:
                data_to_post['start_date'] = None
            url = reverse(self.url_list_api)
            response = self.client.post(url, data_to_post)
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
            self.client.force_authenticate(self.user_admin)
            response = self.client.post(url, data_to_post)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
            self.assertIsNotNone(response.data['id'])
            if perissable:
                self.assertIsNotNone(response.data['start_date'])
        test_class.test_api_post = _test_api_post

    if test_put:
        def _test_api_put(self):
            """
            Méthode de test de modification d'un élément
            """
            mommy_make_args = dict(make_m2m=True)
            if issubclass(model, PerishableEntity):
                mommy_make_args['start_date'] = now()
            item = self.recipes[0].make(**mommy_make_args)
            url = reverse(self.url_detail_api, args=[item.id])
            data_to_put = self.serializer(item).data
            response = self.client.put(url, data_to_put)
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
            self.client.force_authenticate(self.user_admin)
            response = self.client.put(url, data_to_put)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            # Dans le cas des PerishableEntity, on vérifie que l'entité modifiée est en fait "périmée" et
            # qu'une nouvelle entité est créée à la bonne date (pour l'historisation)
            if issubclass(model, PerishableEntity):
                # ancienne entité périmée => possède une end_date
                old_item = model.objects.get(pk=item.id)
                self.assertIsNotNone(old_item.end_date)
                # L'entité retournée doit être une nouvelle entité sans end_date
                self.assertNotEqual(response.data['id'], item.id)
                self.assertIsNone(response.data['end_date'])
            else:
                self.assertEqual(response.data['id'], item.id)
        test_class.test_api_put = _test_api_put

    if test_delete:
        def _test_api_delete(self):
            """
            Méthode de test de suppression d'un élément
            """
            item = self.recipes[0].make()
            url = reverse(self.url_detail_api, args=[item.id])
            response = self.client.delete(url)
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
            self.client.force_authenticate(self.user_admin)
            response = self.client.delete(url)
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.data)
            get_item = model.objects.filter(pk=item.id).first()
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
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
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
            url = reverse(self.url_list_api) + '?order_by=-id'
            response = self.client.get(url)
            self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
            self.client.force_authenticate(self.user_admin)
            response = self.client.get(url)
            item1 = response.data['results'][0]
            item2 = response.data['results'][1]
            self.assertGreater(item1['id'], item2['id'])
            url = reverse(self.url_list_api) + '?order_by=id'
            response = self.client.get(url)
            item1 = response.data['results'][0]
            item2 = response.data['results'][1]
            self.assertLess(item1['id'], item2['id'])
        test_class.test_api_order_by = _test_api_order_by

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
    import os
    import tempfile
    import decimal

    # Classe de test transactionnelle supprimant la fixture à la fin
    class AutoTest(Test):

        @classmethod
        def tearDownClass(cls):
            for fixture in cls.fixtures:
                os.remove(fixture)
            super().tearDownClass()

    # Récupération du nom depuis le fichier
    name, ext = os.path.splitext(os.path.basename(fixture))
    name = ''.join(x.capitalize() or '_' for x in name.split('_')) + 'TestCase'
    # Lecture des données
    with open(fixture, 'r') as file:
        data = json_decode(file.read())
    # Création d'une fixture temporaire à partir des modèles
    models = data.pop('models', {})
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.json') as temp_file:
        temp_file.write(json_encode(models))
        temp_file.flush()
    # Création de la classe de test
    test_class = type(name, (AutoTest, ), dict(data=data, fixtures=[temp_file.name]))
    # Création des méthodes de test
    tests = data.pop('tests')
    for index, test in enumerate(tests, start=1):
        def test_method(self, test=test):
            data = callback(test)
            with self.subTest():
                for result in test.get('results', []):
                    fields = result.get('fields', [])
                    value = result.get('value', None)
                    current = data
                    for field in fields:
                        current = current[field]
                    # Conversion explicite en décimal
                    if isinstance(current, decimal.Decimal):
                        value = decimal.Decimal(str(value))
                    operator = OPERATORS.get(result.get('operator', '=='))
                    operator(self, current, value)
        test_name = 'test_{}'.format(slugify(test.get('name', index)).replace('-', '_'))
        setattr(test_class, test_name, test_method)
    return test_class
