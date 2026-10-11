"""Live smoke test for the two external integrations.

    python manage.py check_integrations
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from interviews.services import create_meet_event, delete_calendar_event, google_calendar_enabled


class Command(BaseCommand):
    help = "Verify the Gemini (resume reading) and Google (Meet) credentials actually work"

    def handle(self, *args, **options):
        self._check_gemini()
        self._check_google()
        self._check_email()

    def _check_gemini(self):
        if not settings.GEMINI_API_KEY:
            self.stdout.write(self.style.WARNING("Resume AI: GEMINI_API_KEY not set -> using offline parser."))
            return
        try:
            import requests
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                json={"contents": [{"parts": [{"text": "Reply with: ok"}]}]}, timeout=30,
            )
            r.raise_for_status()
            self.stdout.write(self.style.SUCCESS(f"Resume AI: OK ({settings.GEMINI_MODEL})"))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Resume AI: FAILED - {exc}"))

    def _check_email(self):
        if not (settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD):
            self.stdout.write(self.style.WARNING("Email: EMAIL_HOST_USER/EMAIL_HOST_PASSWORD not set -> offer emails print to the server console only."))
            return
        try:
            from django.core.mail import get_connection
            connection = get_connection()
            connection.open()
            connection.close()
            self.stdout.write(self.style.SUCCESS(f"Email: OK (logged in to {settings.EMAIL_HOST} as {settings.EMAIL_HOST_USER})"))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Email: FAILED - {exc}"))

    def _check_google(self):
        if not google_calendar_enabled():
            self.stdout.write(self.style.WARNING("Google Meet: not configured -> interviewers paste links manually."))
            return
        link, event_id = create_meet_event(
            summary="Werkbase integration test (safe to ignore)",
            description="Created by check_integrations; deleted immediately.",
            start_at=timezone.now() + timedelta(days=1),
            duration_minutes=15,
            attendee_emails=[],
        )
        if link:
            delete_calendar_event(event_id)
            self.stdout.write(self.style.SUCCESS(f"Google Meet: OK (sample link {link}, test event removed)"))
        else:
            self.stdout.write(self.style.ERROR("Google Meet: FAILED - see the server log above for the Google error."))
