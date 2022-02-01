# coding: utf-8

try:
    from rest_framework_jwt.settings import api_settings

    def jwt_encode_handler(payload):
        """
        Encode handler override for JWT
        """
        import jwt

        return jwt.encode(payload, str(api_settings.JWT_SECRET_KEY), str(api_settings.JWT_ALGORITHM)).decode("utf-8")

    def jwt_decode_handler(token):
        """
        Decode handler override for JWT
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
        Payload handler for JWT
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
        Token payload handler for JWT
        """
        from django.utils.timezone import now

        if user and hasattr(user, "last_login"):
            user.last_login = now()
            user.save(update_fields=["last_login"])
        return {"token": token}

except ImportError:
    pass
