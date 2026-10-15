from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication


class CookieJWTAuthentication(JWTAuthentication):
    """
    DRF SimpleJWT expects the token in an `Authorization: Bearer <token>`
    header by default. We're storing it in an httpOnly cookie instead
    (not readable by JS — see the migration guide's rationale on why
    that's preferable to localStorage for a financial tool), so this
    reads from the cookie, falling back to the header if present
    (useful for testing with curl/Postman without cookie juggling).
    """

    def authenticate(self, request):
        header = self.get_header(request)

        if header is not None:
            raw_token = self.get_raw_token(header)
        else:
            raw_token = request.COOKIES.get(settings.SIMPLE_JWT["AUTH_COOKIE"])

        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token
