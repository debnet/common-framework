# coding: utf-8
from common.models import MetaData, Webhook
from common.tests import create_api_test_class

RECIPES = {}


# Tests automatisés pour tous les modèles liés à une API REST
for model in (MetaData, Webhook):
    create_api_test_class(model, namespace="common-api", data=RECIPES.get(model, None))
