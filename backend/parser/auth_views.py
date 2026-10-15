from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .authentication import CookieJWTAuthentication
from .models import UserProfile


def _serialize_user(user):
    profile = getattr(user, "profile", None)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": profile.role if profile else "viewer",
    }


def _set_auth_cookies(response, access, refresh):
    cfg = settings.SIMPLE_JWT

    response.set_cookie(
        cfg["AUTH_COOKIE"],
        access,
        max_age=int(cfg["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        httponly=cfg["AUTH_COOKIE_HTTP_ONLY"],
        secure=cfg["AUTH_COOKIE_SECURE"],
        samesite=cfg["AUTH_COOKIE_SAMESITE"],
        path="/",
    )
    response.set_cookie(
        cfg["AUTH_COOKIE_REFRESH"],
        refresh,
        max_age=int(cfg["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=cfg["AUTH_COOKIE_HTTP_ONLY"],
        secure=cfg["AUTH_COOKIE_SECURE"],
        samesite=cfg["AUTH_COOKIE_SAMESITE"],
        # Scoped narrower than the access cookie — the refresh token
        # only ever needs to be sent to the refresh endpoint itself.
        path="/api/v1/auth/refresh/",
    )


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        email = (request.data.get("email") or "").strip()
        password = request.data.get("password") or ""

        if not username or not password:
            return Response(
                {"success": False, "error": "Username and password are required."},
                status=400,
            )

        if User.objects.filter(username=username).exists():
            return Response(
                {"success": False, "error": "That username is already taken."},
                status=400,
            )

        try:
            validate_password(password)
        except DjangoValidationError as error:
            return Response(
                {"success": False, "error": " ".join(error.messages)},
                status=400,
            )

        user = User.objects.create_user(username=username, email=email, password=password)

        # Self-registration always defaults to viewer, regardless of
        # anything the client sends — an admin must explicitly upgrade
        # an account to accountant/admin. Never let the registration
        # endpoint itself grant elevated access to a financial tool.
        user.profile.role = UserProfile.ROLE_VIEWER
        user.profile.save()

        refresh = RefreshToken.for_user(user)
        access = str(refresh.access_token)

        response = Response(
            {"success": True, "user": _serialize_user(user)}, status=201
        )
        _set_auth_cookies(response, access, str(refresh))
        return response


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = TokenObtainPairSerializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {"success": False, "error": "Invalid username or password."},
                status=401,
            )

        access = str(serializer.validated_data["access"])
        refresh = str(serializer.validated_data["refresh"])
        user = serializer.user

        response = Response({"success": True, "user": _serialize_user(user)})
        _set_auth_cookies(response, access, refresh)
        return response


class RefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw_refresh = request.COOKIES.get(settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"])

        if not raw_refresh:
            return Response(
                {"success": False, "error": "No refresh token."}, status=401
            )

        try:
            refresh = RefreshToken(raw_refresh)
            access = str(refresh.access_token)
        except TokenError:
            return Response(
                {"success": False, "error": "Refresh token invalid or expired."},
                status=401,
            )

        response = Response({"success": True})
        cfg = settings.SIMPLE_JWT
        response.set_cookie(
            cfg["AUTH_COOKIE"],
            access,
            max_age=int(cfg["ACCESS_TOKEN_LIFETIME"].total_seconds()),
            httponly=cfg["AUTH_COOKIE_HTTP_ONLY"],
            secure=cfg["AUTH_COOKIE_SECURE"],
            samesite=cfg["AUTH_COOKIE_SAMESITE"],
            path="/",
        )
        return response


class LogoutView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        response = Response({"success": True})
        response.delete_cookie(settings.SIMPLE_JWT["AUTH_COOKIE"], path="/")
        response.delete_cookie(
            settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"],
            path="/api/v1/auth/refresh/",
        )
        return response


class MeView(APIView):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"success": True, "user": _serialize_user(request.user)})
