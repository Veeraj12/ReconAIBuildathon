"""
End-to-end contract tests for the cookie-JWT auth surface and the
reconciliation-run restoration endpoint.

These exist as the regression suite for the frontend login→dashboard
audit: the deployed failure was NOT in these backend boundaries (all of
them were verified correct — login 200 + Set-Cookie, /auth/me 401
without cookies, CORS with credentials) but these tests now pin the
contract so future changes can't silently break it.

They run against the default sqlite fallback database (no DATABASE_URL
needed), exercising the real URLconf, serializers, and
CookieJWTAuthentication — no fakes below the view layer.
"""

from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from .models import ReconciliationRun

AUTH_ME_URL = "/api/v1/auth/me/"
LOGIN_URL = "/api/v1/auth/login/"
REGISTER_URL = "/api/v1/auth/register/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
LATEST_URL = "/api/v1/reconciliation-runs/latest/"

SAMPLE_RESULT = {"summary": {"matched": 10, "mismatched": 2}}


class LoginViewTests(TestCase):
    """POST /api/v1/auth/login/ — cookie issuing is the core contract."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=True)
        User.objects.create_user(username="testadmin", password="StrongPass123!")

    def test_wrong_credentials_return_401_json(self):
        response = self.client.post(
            LOGIN_URL, {"username": "testadmin", "password": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["success"])

    def test_successful_login_returns_user_and_sets_cookies(self):
        response = self.client.post(
            LOGIN_URL, {"username": "testadmin", "password": "StrongPass123!"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["user"]["username"], "testadmin")
        # Frontend `AuthUser` expects exactly these four keys.
        self.assertEqual(
            set(body["user"].keys()), {"id", "username", "email", "role"}
        )
        # Both JWTs must be issued as httpOnly cookies…
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)
        access = response.cookies["access_token"]
        refresh = response.cookies["refresh_token"]
        self.assertTrue(access["httponly"])
        self.assertTrue(refresh["httponly"])
        # …with production-safe attributes (DEBUG is False in settings).
        self.assertEqual(access["samesite"], "None")
        self.assertTrue(access["secure"])
        # The refresh cookie must be scoped to the refresh endpoint only.
        self.assertEqual(refresh["path"], "/api/v1/auth/refresh/")
        self.assertEqual(access["path"], "/")

    def test_cookies_set_by_login_authenticate_me_request(self):
        self.client.post(
            LOGIN_URL, {"username": "testadmin", "password": "StrongPass123!"}, format="json"
        )
        # APIClient resends the stored cookies — mimicking the browser
        # attaching its httpOnly cookies to the next /auth/me/ call.
        response = self.client.get(AUTH_ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "testadmin")


class MeViewTests(TestCase):
    """GET /api/v1/auth/me/ — 401 vs 200 is the guard's decision input."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=True)

    def test_unauthenticated_returns_401(self):
        response = self.client.get(AUTH_ME_URL)
        self.assertEqual(response.status_code, 401)

    def test_valid_access_cookie_returns_user(self):
        User.objects.create_user(username="me_user", password="StrongPass123!")
        self.client.post(
            LOGIN_URL, {"username": "me_user", "password": "StrongPass123!"}, format="json"
        )
        response = self.client.get(AUTH_ME_URL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["user"]["username"], "me_user")
        # Default role for a fresh account is viewer.
        self.assertEqual(body["user"]["role"], "viewer")


class RefreshViewTests(TestCase):
    """POST /api/v1/auth/refresh/ — the api-client's 401-recovery path."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=True)
        User.objects.create_user(username="refresher", password="StrongPass123!")

    def _login(self):
        return self.client.post(
            LOGIN_URL, {"username": "refresher", "password": "StrongPass123!"}, format="json"
        )

    def test_without_refresh_cookie_returns_401(self):
        response = self.client.post(REFRESH_URL)
        self.assertEqual(response.status_code, 401)

    def test_with_refresh_cookie_issues_new_access_cookie(self):
        self._login()
        response = self.client.post(REFRESH_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.cookies)
        self.assertTrue(response.cookies["access_token"]["httponly"])


class LogoutViewTests(TestCase):
    """POST /api/v1/auth/logout/ — both cookies must be cleared."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=True)
        User.objects.create_user(username="quitter", password="StrongPass123!")

    def test_logout_clears_cookies(self):
        self.client.post(
            LOGIN_URL, {"username": "quitter", "password": "StrongPass123!"}, format="json"
        )
        response = self.client.post(LOGOUT_URL)
        self.assertEqual(response.status_code, 200)
        # Expired max_age/epoch delete cookies rather than absent ones.
        self.assertLessEqual(response.cookies["access_token"].value, "")
        self.assertLessEqual(response.cookies["refresh_token"].value, "")
        # And after logout the session is really gone.
        me = self.client.get(AUTH_ME_URL)
        self.assertEqual(me.status_code, 401)


class RegisterViewTests(TestCase):
    """POST /api/v1/auth/register/ — viewer role + cookie issuance."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=True)

    def _register(self, username="newemail@example.com"):
        return self.client.post(
            REGISTER_URL,
            {"username": username, "email": "newemail@example.com", "password": "StrongPass123!"},
            format="json",
        )

    def test_registration_creates_viewer_and_cookies(self):
        response = self._register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["role"], "viewer")
        self.assertIn("access_token", response.cookies)

        # An already-issued session works immediately.
        me = self.client.get(AUTH_ME_URL)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["role"], "viewer")

    def test_duplicate_username_returns_400_not_500(self):
        self._register()
        response = self._register()
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["error"].lower())


class LatestReconciliationRunTests(TestCase):
    """GET /api/v1/reconciliation-runs/latest/ — restoration contract."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=True)
        self.user = User.objects.create_user(username="runner", password="StrongPass123!")
        self.other = User.objects.create_user(username="other", password="StrongPass123!")

    def _login(self):
        self.client.post(
            LOGIN_URL, {"username": "runner", "password": "StrongPass123!"}, format="json"
        )

    def test_empty_state_is_success_with_null_run(self):
        # A user who has never reconciled is a normal state — the
        # frontend depends on `run: null` (NOT an error) to fall back
        # silently to the empty upload form.
        self._login()
        response = self.client.get(LATEST_URL + "?type=single")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertIsNone(body["run"])

    def test_returns_own_latest_run_result(self):
        ReconciliationRun.objects.create(
            user=self.user, run_type=ReconciliationRun.RUN_TYPE_SINGLE, result=SAMPLE_RESULT
        )
        older = ReconciliationRun.objects.create(
            user=self.user, run_type=ReconciliationRun.RUN_TYPE_SINGLE, result={"summary": {"matched": 1}}
        )
        # Force a strictly-older created_at so ordering is unambiguous.
        ReconciliationRun.objects.filter(pk=older.pk).update(
            created_at=older.created_at - __import__("datetime").timedelta(days=1)
        )
        self._login()
        response = self.client.get(LATEST_URL + "?type=single")
        self.assertEqual(response.status_code, 200)
        run = response.json()["run"]
        self.assertEqual(run["runType"], "single")
        self.assertEqual(run["result"]["summary"], SAMPLE_RESULT["summary"])

    def test_type_filter_selects_matching_run_type(self):
        ReconciliationRun.objects.create(
            user=self.user, run_type=ReconciliationRun.RUN_TYPE_SINGLE, result=SAMPLE_RESULT
        )
        self._login()
        response = self.client.get(LATEST_URL + "?type=multi")
        self.assertEqual(response.json()["run"], None)

    def test_other_users_runs_are_never_visible(self):
        ReconciliationRun.objects.create(
            user=self.other, run_type=ReconciliationRun.RUN_TYPE_SINGLE, result=SAMPLE_RESULT
        )
        self._login()
        response = self.client.get(LATEST_URL + "?type=single")
        self.assertIsNone(response.json()["run"])

    def test_unauthenticated_returns_401(self):
        response = self.client.get(LATEST_URL + "?type=single")
        self.assertEqual(response.status_code, 401)
