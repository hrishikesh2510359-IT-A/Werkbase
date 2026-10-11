"""
Real "Sign in with Google" (OAuth 2.0 authorization-code flow + PKCE).

Security model - Google only proves *who the person is* (a verified email):
  * The Werkbase account must already exist. Nothing is auto-created here, so
    Google can never mint a staff account or grant a role.
  * The role always comes from our database, never from Google.
  * The portal boundary is kept: the candidate portal only admits candidate
    accounts, the company portal only admits company accounts.
  * `state` (CSRF) is checked, and only a verified Google email is accepted.
"""

import logging
import os

from django.conf import settings
from django.contrib.auth import login
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import User

logger = logging.getLogger(__name__)

SESSION_KEY = "google_login"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
PORTALS = {"candidate": "candidate_login", "company": "company_login"}


def google_login_enabled():
    return bool(settings.GOOGLE_LOGIN_CLIENT_ID and settings.GOOGLE_LOGIN_CLIENT_SECRET)


def _flow(request):
    from google_auth_oauthlib.flow import Flow

    redirect_uri = request.build_absolute_uri(reverse("google_callback"))
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_LOGIN_CLIENT_ID,
                "client_secret": settings.GOOGLE_LOGIN_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )


def _fail(request, portal, message):
    request.session["login_error"] = message
    return redirect(PORTALS.get(portal, "portal_select"))


@require_GET
def google_login_start(request, portal):
    if portal not in PORTALS:
        return redirect("portal_select")
    if request.user.is_authenticated:
        return redirect("dashboard")
    if not google_login_enabled():
        return _fail(request, portal, "Google sign-in isn't configured on this server.")

    flow = _flow(request)
    url, state = flow.authorization_url(prompt="select_account", access_type="online")
    request.session[SESSION_KEY] = {"state": state, "portal": portal, "code_verifier": flow.code_verifier}
    return redirect(url)


def _fetch_google_identity(request, saved):
    """Exchanges the authorization code and returns the verified ID-token claims.
    Isolated so tests can mock the network round-trip."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    if settings.DEBUG:
        # Local dev runs on plain http://localhost; oauthlib refuses that by default.
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    # Google may return the scopes in a different order / with openid expanded.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

    flow = _flow(request)
    flow.code_verifier = saved["code_verifier"]
    flow.fetch_token(authorization_response=request.build_absolute_uri())
    return id_token.verify_oauth2_token(
        flow.credentials.id_token, google_requests.Request(), settings.GOOGLE_LOGIN_CLIENT_ID
    )


@require_GET
def google_login_callback(request):
    saved = request.session.pop(SESSION_KEY, None)
    if not saved:
        return _fail(request, None, "Google sign-in session expired. Please try again.")
    portal = saved["portal"]

    if request.GET.get("error"):
        return _fail(request, portal, "Google sign-in was cancelled.")
    # CSRF protection: the state must be exactly what we issued for this browser.
    if not request.GET.get("state") or request.GET["state"] != saved["state"]:
        return _fail(request, portal, "Google sign-in could not be verified. Please try again.")

    try:
        identity = _fetch_google_identity(request, saved)
    except Exception:
        logger.exception("Google sign-in token exchange failed")
        return _fail(request, portal, "Google sign-in failed. Please try again.")

    email = (identity.get("email") or "").strip()
    if not email or not identity.get("email_verified"):
        return _fail(request, portal, "Your Google email isn't verified.")

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user is None:
        message = (
            "No Werkbase account matches that Google email. Sign up first."
            if portal == "candidate"
            else "No company account matches that Google email. Ask your HR Admin to create one."
        )
        return _fail(request, portal, message)

    # Portal boundary, same rule as the password login views.
    is_candidate = user.role == User.Role.CANDIDATE
    if (portal == "candidate") != is_candidate:
        return _fail(request, portal, "That account belongs to the other portal. Use the matching login page.")

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("dashboard")
