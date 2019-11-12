# coding: utf-8
from common.tests import create_api_test_class
from common.models import MetaData, Webhook


RECIPES = {}


# Automated tests for all models related to a REST API
for model in [MetaData, Webhook]:
    create_api_test_class(model, namespace='common-api', data=RECIPES.get(model, None))
