"""One-time Google sign-in that produces the refresh token for Meet links.

    python manage.py google_auth

Opens a browser, you approve access to Google Calendar for the account that
will own the interview events, and the three values are saved into .env.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from interviews.constants import GOOGLE_CALENDAR_SCOPES
from interviews.envfile import upsert_env


class Command(BaseCommand):
    help = "Authorize Google Calendar once and store the refresh token in .env"

    def add_arguments(self, parser):
        parser.add_argument("--client-id", default=settings.GOOGLE_OAUTH_CLIENT_ID)
        parser.add_argument("--client-secret", default=settings.GOOGLE_OAUTH_CLIENT_SECRET)

    def handle(self, *args, **options):
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_id = options["client_id"] or input("Google OAuth client ID: ").strip()
        client_secret = options["client_secret"] or input("Google OAuth client secret: ").strip()
        if not client_id or not client_secret:
            raise CommandError("Client ID and secret are required (see .env.example).")

        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            },
            scopes=GOOGLE_CALENDAR_SCOPES,
        )
        self.stdout.write("A browser window will open - sign in and click Allow.")
        # access_type=offline + prompt=consent guarantees Google returns a refresh token.
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        if not creds.refresh_token:
            raise CommandError("Google did not return a refresh token. Revoke the app's access and retry.")

        path = upsert_env({
            "GOOGLE_OAUTH_CLIENT_ID": client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
            "GOOGLE_OAUTH_REFRESH_TOKEN": creds.refresh_token,
        })
        self.stdout.write(self.style.SUCCESS(f"Saved Google credentials to {path}. Restart the server, then run: python manage.py check_integrations"))
