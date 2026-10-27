# coding: utf-8
"""Hooks JWT historiques, définis seulement si rest_framework_jwt est installé.

Leur présence ne configure aucune authentification automatiquement. Les API
de signature/décodage ciblent l'intégration JWT legacy et ses dépendances.
"""

try:
    from rest_framework_jwt.settings import api_settings

    def jwt_encode_handler(payload):
        """
        Signe un payload selon la clé et l'algorithme de rest_framework_jwt.

        Attend l'API PyJWT historique renvoyant des bytes, convertis en UTF-8.
        Les incompatibilités d'API et erreurs cryptographiques sont propagées.
        """
        import jwt

        return jwt.encode(
            payload, str(api_settings.JWT_SECRET_KEY), str(api_settings.JWT_ALGORITHM)
        ).decode("utf-8")

    def jwt_decode_handler(token):
        """
        Décode et valide un jeton selon les options JWT du projet.

        Transmet les règles d'expiration, audience, issuer et leeway à PyJWT.
        Les erreurs de jeton sont laissées à la couche d'authentification appelante.
        """
        options = {
            "verify_exp": api_settings.JWT_VERIFY_EXPIRATION,
        }

        import jwt

        return jwt.decode(
            token,
            str(api_settings.JWT_SECRET_KEY),
            str(api_settings.JWT_VERIFY),
            options=options,
            leeway=api_settings.JWT_LEEWAY,
            audience=api_settings.JWT_AUDIENCE,
            issuer=api_settings.JWT_ISSUER,
            algorithms=[api_settings.JWT_ALGORITHM],
        )

    def jwt_payload_handler(user):
        """
        Complète le payload JWT standard avec l'identité et les indicateurs de rôle.

        Les rôles sont un instantané au moment de l'émission ; leurs changements
        ultérieurs ne révoquent pas automatiquement un jeton déjà signé.
        """
        from rest_framework_jwt.utils import jwt_payload_handler as payload_handler

        payload = payload_handler(user)
        payload.update(
            user_id=user.pk,
            email=getattr(user, "email", None),
            is_staff=getattr(user, "is_staff", None),
            is_superuser=getattr(user, "is_superuser", None),
        )
        return payload

    def jwt_response_payload_handler(token, user, request):
        """
        Met à jour last_login si disponible puis retourne {'token': token}.

        La sauvegarde partielle du compte est un effet de bord de la réponse
        d'authentification ; aucune rotation/révocation de jetons n'est réalisée ici.
        """
        from django.utils.timezone import now

        if user and hasattr(user, "last_login"):
            user.last_login = now()
            user.save(update_fields=["last_login"])
        return {"token": token}

except ImportError:
    pass
