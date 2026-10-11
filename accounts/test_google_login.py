"""Sign in with Google: the account must already exist, the role comes from
our DB, and the portal boundary holds. Google's network round-trip is mocked."""

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User

CONFIGURED = dict(GOOGLE_LOGIN_CLIENT_ID="cid", GOOGLE_LOGIN_CLIENT_SECRET="sec")


def make(email, role):
    return User.objects.create_user(email=email, password="Test@1234", full_name="N", role=role)


@override_settings(**CONFIGURED)
class GoogleLoginTests(TestCase):
    def setUp(self):
        self.staff = make("staff@werkbase.com", User.Role.HR_RECRUITER)
        self.cand = make("cand@gmail.com", User.Role.CANDIDATE)

    def _begin(self, portal):
        """Starts the flow and returns the state Google would echo back."""
        with mock.patch("accounts.google_login._flow") as flow:
            flow.return_value.authorization_url.return_value = ("https://accounts.google.com/x", "STATE123")
            flow.return_value.code_verifier = "verifier"
            response = self.client.get(reverse("google_login", args=[portal]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://accounts.google.com"))
        return "STATE123"

    def _callback(self, state, identity, **extra):
        with mock.patch("accounts.google_login._fetch_google_identity", return_value=identity):
            return self.client.get(reverse("google_callback"), {"state": state, "code": "c", **extra})

    def _logged_in(self):
        return "_auth_user_id" in self.client.session

    def test_company_staff_signs_in_with_google(self):
        state = self._begin("company")
        response = self._callback(state, {"email": "STAFF@werkbase.com", "email_verified": True})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)  # case-insensitive match

    def test_candidate_signs_in_with_google_on_candidate_portal(self):
        state = self._begin("candidate")
        self._callback(state, {"email": "cand@gmail.com", "email_verified": True})
        self.assertTrue(self._logged_in())

    def test_unknown_email_is_not_auto_created(self):
        state = self._begin("company")
        response = self._callback(state, {"email": "stranger@gmail.com", "email_verified": True})
        self.assertFalse(self._logged_in())
        self.assertFalse(User.objects.filter(email="stranger@gmail.com").exists())
        self.assertRedirects(response, reverse("company_login"), fetch_redirect_response=False)

    def test_candidate_cannot_use_company_portal_and_vice_versa(self):
        state = self._begin("company")
        self._callback(state, {"email": "cand@gmail.com", "email_verified": True})
        self.assertFalse(self._logged_in())
        state = self._begin("candidate")
        self._callback(state, {"email": "staff@werkbase.com", "email_verified": True})
        self.assertFalse(self._logged_in())

    def test_unverified_google_email_is_rejected(self):
        state = self._begin("company")
        self._callback(state, {"email": "staff@werkbase.com", "email_verified": False})
        self.assertFalse(self._logged_in())

    def test_state_mismatch_is_rejected(self):
        self._begin("company")
        self._callback("FORGED", {"email": "staff@werkbase.com", "email_verified": True})
        self.assertFalse(self._logged_in())

    def test_callback_without_started_flow_is_rejected(self):
        self._callback("STATE123", {"email": "staff@werkbase.com", "email_verified": True})
        self.assertFalse(self._logged_in())

    def test_user_denying_consent_is_handled(self):
        state = self._begin("company")
        response = self.client.get(reverse("google_callback"), {"state": state, "error": "access_denied"})
        self.assertFalse(self._logged_in())
        self.assertEqual(response.status_code, 302)

    def test_exchange_failure_does_not_log_in(self):
        state = self._begin("company")
        with mock.patch("accounts.google_login._fetch_google_identity", side_effect=RuntimeError("boom")):
            self.client.get(reverse("google_callback"), {"state": state, "code": "c"})
        self.assertFalse(self._logged_in())

    def test_error_message_is_shown_on_login_page(self):
        state = self._begin("company")
        self._callback(state, {"email": "stranger@gmail.com", "email_verified": True})
        page = self.client.get(reverse("company_login"))
        self.assertContains(page, "No company account matches")

    def test_buttons_visible_only_when_configured(self):
        self.assertContains(self.client.get(reverse("candidate_login")), reverse("google_login", args=["candidate"]))
        with override_settings(GOOGLE_LOGIN_CLIENT_ID=""):
            self.assertNotContains(self.client.get(reverse("candidate_login")), "Sign in with Google")

    def test_start_when_unconfigured_fails_gracefully(self):
        with override_settings(GOOGLE_LOGIN_CLIENT_ID=""):
            response = self.client.get(reverse("google_login", args=["company"]))
        self.assertRedirects(response, reverse("company_login"), fetch_redirect_response=False)
        self.assertFalse(self._logged_in())

    def test_unknown_portal_is_ignored(self):
        self.assertRedirects(self.client.get(reverse("google_login", args=["admin"])), reverse("portal_select"), fetch_redirect_response=False)
