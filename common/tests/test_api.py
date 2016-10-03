# coding: utf-8
from common.tests import create_api_test_class
from common.models import MetaData, Webhook


RECIPES = {}


# Tests automatisées pour tous les modèles liés à une API REST
for model in [MetaData, Webhook]:
    has_metadatas = any(f.name == 'metadatas' and f.related_model is MetaData for f in model._meta.private_fields)
    vars()['{}_{}'.format(model._meta.app_label, model._meta.model_name)] = \
        create_api_test_class(model, data=RECIPES.get(model, None), test_metadatas=has_metadatas)
