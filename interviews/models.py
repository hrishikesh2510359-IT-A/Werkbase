from datetime import timedelta

from django.conf import settings
from django.db import models

from job_postings.models import Application

from .constants import DEFAULT_INTERVIEW_DURATION_MINUTES


class Interview(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class Recommendation(models.TextChoices):
        PROCEED = "PROCEED", "Proceed"
        HOLD = "HOLD", "Hold"
        REJECT = "REJECT", "Reject"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="interviews")

    # Security boundary: always set server-side from request.user in the view,
    # never taken from submitted form data. Interviewers self-serve - nobody
    # assigns an interview to someone else.
    interviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="interviews_conducted"
    )

    # Stored in UTC (USE_TZ=True); rendered in the project's local timezone.
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=DEFAULT_INTERVIEW_DURATION_MINUTES)

    meet_link = models.URLField(blank=True)
    # True when Google Calendar couldn't mint a link and a human pasted one in,
    # so it's auditable which interviews bypassed auto-generation.
    is_manual_link = models.BooleanField(default=False)
    # Kept so a reschedule can delete the stale calendar event instead of
    # leaving an orphaned invite in the interviewer's calendar.
    calendar_event_id = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)

    # --- Feedback (filled in after the interview) --------------------------
    # Stored here so the future Hiring Manager review screen can read it
    # without any schema change.
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback_comment = models.TextField(blank=True)
    recommendation = models.CharField(max_length=16, choices=Recommendation.choices, blank=True)
    feedback_submitted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_at"]

    def __str__(self):
        return f"{self.application.candidate.full_name} - {self.scheduled_at:%Y-%m-%d %H:%M}"

    @property
    def ends_at(self):
        return self.scheduled_at + timedelta(minutes=self.duration_minutes)

    @property
    def can_join(self):
        """The Join button is shown for any scheduled interview that has a
        link - no time window. Whoever clicks early just waits in the Meet
        lobby; hiding the link made it look like no link had been generated."""
        return self.status == self.Status.SCHEDULED and bool(self.meet_link)


class Notification(models.Model):
    """Minimal in-portal notification.

    The Application module had no notification pattern to reuse (checked - none
    existed; Django's `messages` are session-scoped and can't reach another
    user), so this is deliberately generic: any module can write one.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.CharField(max_length=300)
    # Relative URL the notification points at, e.g. "/jobs/applications/mine/".
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    # The live pop-up (toast) is shown once per notification; this records it so
    # a page reload never re-pops old news. Independent of is_read (the bell).
    popup_shown = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"To {self.recipient.full_name}: {self.message[:40]}"
