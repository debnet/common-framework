# coding: utf-8
from common.tests import create_api_test_class
from common.models import MetaData, Webhook


RECIPES = {}


# Tests automatisées pour tous les modèles liés à une API REST
for model in [MetaData, Webhook]:
    vars()['{}_{}'.format(model._meta.app_label, model._meta.model_name)] = \
        create_api_test_class(model, data=RECIPES.get(model, None))
