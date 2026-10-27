# coding: utf-8
"""Décorateurs transactionnels pour vues API synchrones."""

from functools import wraps

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response


def atomic(function=None, *, using=None, savepoint=True):
    """Exécute une vue et sa sérialisation dans une transaction Django.

    Utilisable avec @atomic ou @atomic(using="alias"), sous @api_view pour
    laisser DRF traiter les exceptions après rollback. Toute exception annule
    les écritures ; une réponse HTTP de statut >= 400 les annule également.
    Les ValidationError Django deviennent des ValidationError DRF après sortie
    de la transaction. Les autres exceptions, notamment les erreurs serveur,
    sont propagées sans être requalifiées en erreurs client.

    Une Response finalisée est rendue avant commit avec son renderer ; avant
    finalisation DRF, ses données sont prévalidées avec JSONRenderer, sans
    marquer la réponse comme rendue. Le rendu HTML/CSV ultérieur n'est donc pas
    couvert dans ce second cas. Les réponses streaming et effets externes
    (réseau, fichiers, caches) ne sont pas transactionnels. Utiliser on_commit
    pour déclencher des effets uniquement après validation de la transaction.

    Une transaction englobante peut encore annuler les écritures après retour.
    Aucun mode simulation implicite ni transaction multi-base n'est ajouté.

    :param function: Vue/fonction synchrone à décorer
    :param using: Alias de la transaction, default si absent ; ne redirige pas
        automatiquement les requêtes ORM vers cet alias
    :param savepoint: Créer un savepoint lors d'une transaction imbriquée
    :return: Fonction décorée conservant ses attributs avec wraps
    """

    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            try:
                with transaction.atomic(using=using, savepoint=savepoint):
                    result = func(*args, **kwargs)
                    if getattr(result, "status_code", 0) >= 400:
                        transaction.set_rollback(True, using=using)
                    elif isinstance(result, Response) and not result.is_rendered:
                        if getattr(result, "accepted_renderer", None) is not None:
                            result.render()
                        else:
                            JSONRenderer().render(result.data)
                    return result
            except DjangoValidationError as error:
                detail = (
                    error.message_dict
                    if hasattr(error, "message_dict")
                    else error.messages
                )
                raise ValidationError(detail) from error

        return wrapped

    return decorate(function) if function is not None else decorate
