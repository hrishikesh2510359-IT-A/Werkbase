"""
Interview scheduling services: Google Calendar/Meet integration, the
double-booking guard, notifications and application-status sync.

The Google calls are deliberately isolated behind create_meet_event() /
delete_calendar_event() so they're trivial to mock in tests, and so a Google
outage can never take scheduling down with it.
"""

import logging
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from job_postings.models import Application

from .constants import (
    DOUBLE_BOOKING_BUFFER_MINUTES,
    GOOGLE_CALENDAR_ID,
    GOOGLE_CALENDAR_SCOPES,
    GOOGLE_CONFERENCE_DATA_VERSION,
)
from .models import Interview, Notification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google Calendar / Meet
# ---------------------------------------------------------------------------

def google_calendar_enabled():
    """True only when a readable service-account key is configured.

    Credentials come from settings (which read env vars) - never hardcoded.
    When unconfigured we skip the API entirely rather than attempting a call
    that would hang the request before failing.
    """
    path = getattr(settings, "GOOGLE_CALENDAR_CREDENTIALS_FILE", "")
    return bool(path) and os.path.exists(path)


def _calendar_client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_CALENDAR_CREDENTIALS_FILE, scopes=GOOGLE_CALENDAR_SCOPES
    )
    # Minting a Meet link requires acting as a real Workspace user; a bare
    # service account has no calendar of its own to attach a conference to.
    impersonate = getattr(settings, "GOOGLE_CALENDAR_IMPERSONATE_USER", "")
    if impersonate:
        credentials = credentials.with_subject(impersonate)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def create_meet_event(*, summary, description, start_at, duration_minutes, attendee_emails):
    """Creates a calendar event with a Meet link.

    Returns (meet_link, event_id), or (None, None) if Google is unconfigured or
    the call fails for any reason. Callers MUST treat (None, None) as "fall back
    to a manual link" - scheduling is never blocked on Google being reachable.
    """
    if not google_calendar_enabled():
        return None, None

    try:
        service = _calendar_client()
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_at.isoformat(), "timeZone": "UTC"},
            "end": {
                "dateTime": (start_at + timedelta(minutes=duration_minutes)).isoformat(),
                "timeZone": "UTC",
            },
            "attendees": [{"email": email} for email in attendee_emails if email],
            "conferenceData": {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        event = (
            service.events()
            .insert(
                calendarId=GOOGLE_CALENDAR_ID,
                body=body,
                conferenceDataVersion=GOOGLE_CONFERENCE_DATA_VERSION,
                sendUpdates="all",
            )
            .execute()
        )
        link = event.get("hangoutLink") or ""
        if not link:
            # Event created but no Meet link came back (common without a
            # Workspace domain) - treat as failure so the manual path kicks in.
            logger.warning("Calendar event %s created without a Meet link", event.get("id"))
            return None, event.get("id", "")
        return link, event.get("id", "")
    except Exception:
        # Quota, auth, network - any failure degrades to the manual-link path.
        logger.exception("Google Calendar event creation failed; falling back to manual link")
        return None, None


def delete_calendar_event(event_id):
    """Best-effort cleanup so a reschedule doesn't leave a stale invite behind."""
    if not event_id or not google_calendar_enabled():
        return False
    try:
        _calendar_client().events().delete(
            calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, sendUpdates="all"
        ).execute()
        return True
    except Exception:
        logger.exception("Failed to delete calendar event %s", event_id)
        return False


# ---------------------------------------------------------------------------
# Double-booking guard
# ---------------------------------------------------------------------------

def find_conflicting_interview(interviewer, start_at, duration_minutes, exclude_pk=None):
    """Returns an existing SCHEDULED interview whose window overlaps, else None.

    Enforced server-side (never just in the form widget). Each booking is padded
    by DOUBLE_BOOKING_BUFFER_MINUTES on both sides, so two interviews that merely
    touch end-to-start still count as a conflict.
    """
    buffer_delta = timedelta(minutes=DOUBLE_BOOKING_BUFFER_MINUTES)
    new_start = start_at - buffer_delta
    new_end = start_at + timedelta(minutes=duration_minutes) + buffer_delta

    candidates = Interview.objects.filter(
        interviewer=interviewer, status=Interview.Status.SCHEDULED
    ).select_related("application__job_posting")
    if exclude_pk is not None:
        candidates = candidates.exclude(pk=exclude_pk)

    for existing in candidates:
        # Standard overlap test: two windows overlap iff each starts before the
        # other ends.
        if new_start < existing.ends_at and existing.scheduled_at < new_end:
            return existing
    return None


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(recipient, message, link=""):
    """Writes an in-portal notification. No email - none is wired up project-wide."""
    return Notification.objects.create(recipient=recipient, message=message, link=link)


# ---------------------------------------------------------------------------
# Application status sync
# ---------------------------------------------------------------------------

def set_application_status(application, status):
    application.status = status
    application.save(update_fields=["status"])


def schedule_interview(*, application, interviewer, scheduled_at, duration_minutes, manual_link=""):
    """Creates the interview, attaches a Meet link, syncs status and notifies.

    Assumes the caller has already validated permissions, shortlisted state and
    double-booking - this function performs the write, not the policy checks.
    """
    meet_link, event_id = create_meet_event(
        summary=f"Interview - {application.job_posting.job_title}",
        description=f"Werkbase interview with {application.candidate.full_name}.",
        start_at=scheduled_at,
        duration_minutes=duration_minutes,
        attendee_emails=[application.candidate.email, interviewer.email],
    )

    # Fallback: Google unavailable/unconfigured, so use the link the interviewer
    # supplied and flag it, rather than blocking the schedule entirely.
    is_manual = meet_link is None
    if is_manual:
        meet_link = manual_link

    interview = Interview.objects.create(
        application=application,
        interviewer=interviewer,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        meet_link=meet_link or "",
        is_manual_link=is_manual,
        calendar_event_id=event_id or "",
    )

    set_application_status(application, Application.Status.INTERVIEW_SCHEDULED)
    notify(
        application.candidate,
        f"Interview scheduled for {application.job_posting.job_title} on "
        f"{timezone.localtime(scheduled_at):%d %b %Y at %I:%M %p}.",
        link="/jobs/applications/mine/",
    )
    return interview


def reschedule_interview(interview, *, scheduled_at, duration_minutes, manual_link=""):
    """Moves an interview and mints a *fresh* Meet link.

    A new calendar event rather than an edit, per the requirement to avoid stale
    invites; the old event is deleted best-effort.
    """
    delete_calendar_event(interview.calendar_event_id)

    meet_link, event_id = create_meet_event(
        summary=f"Interview - {interview.application.job_posting.job_title}",
        description=f"Werkbase interview with {interview.application.candidate.full_name}.",
        start_at=scheduled_at,
        duration_minutes=duration_minutes,
        attendee_emails=[interview.application.candidate.email, interview.interviewer.email],
    )
    is_manual = meet_link is None
    if is_manual:
        meet_link = manual_link or interview.meet_link

    interview.scheduled_at = scheduled_at
    interview.duration_minutes = duration_minutes
    interview.meet_link = meet_link or ""
    interview.is_manual_link = is_manual
    interview.calendar_event_id = event_id or ""
    interview.save(update_fields=[
        "scheduled_at", "duration_minutes", "meet_link", "is_manual_link",
        "calendar_event_id", "updated_at",
    ])

    notify(
        interview.application.candidate,
        f"Interview for {interview.application.job_posting.job_title} moved to "
        f"{timezone.localtime(scheduled_at):%d %b %Y at %I:%M %p}.",
        link="/jobs/applications/mine/",
    )
    return interview


def cancel_interview(interview):
    """Cancels, notifies the candidate and reverts the application to Shortlisted."""
    delete_calendar_event(interview.calendar_event_id)

    interview.status = Interview.Status.CANCELLED
    interview.save(update_fields=["status", "updated_at"])

    # Back to Shortlisted so the candidate re-enters the schedulable pool.
    set_application_status(interview.application, Application.Status.SHORTLISTED)
    notify(
        interview.application.candidate,
        f"Your interview for {interview.application.job_posting.job_title} was cancelled. "
        "You're still shortlisted - a new slot may be arranged.",
        link="/jobs/applications/mine/",
    )
    return interview


def submit_feedback(interview, *, rating, comment, recommendation):
    """Records feedback, completes the interview and marks the application Interviewed."""
    interview.rating = rating
    interview.feedback_comment = comment
    interview.recommendation = recommendation
    interview.feedback_submitted_at = timezone.now()
    interview.status = Interview.Status.COMPLETED
    interview.save(update_fields=[
        "rating", "feedback_comment", "recommendation",
        "feedback_submitted_at", "status", "updated_at",
    ])

    set_application_status(interview.application, Application.Status.INTERVIEWED)
    return interview
