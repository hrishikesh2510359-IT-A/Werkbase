from datetime import timedelta

from django import forms
from django.utils import timezone

from .constants import (
    DEFAULT_INTERVIEW_DURATION_MINUTES,
    MIN_SCHEDULING_LEAD_MINUTES,
    RATING_MAX,
    RATING_MIN,
)
from .models import Interview

DURATION_CHOICES = [(15, "15 minutes"), (30, "30 minutes"), (45, "45 minutes"), (60, "60 minutes")]


class InterviewScheduleForm(forms.Form):
    scheduled_at = forms.DateTimeField(
        label="Date & time",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )
    duration_minutes = forms.TypedChoiceField(
        label="Duration", choices=DURATION_CHOICES, coerce=int,
        initial=DEFAULT_INTERVIEW_DURATION_MINUTES,
    )
    # Only used when Google Calendar can't mint a link (see services.create_meet_event).
    manual_link = forms.URLField(
        label="Meeting link", required=False,
        help_text="Used only if a Google Meet link can't be generated automatically.",
    )

    def clean_scheduled_at(self):
        scheduled_at = self.cleaned_data["scheduled_at"]
        # The datetime-local widget submits naive local time; attach the project
        # timezone so it's stored correctly as UTC.
        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())

        earliest = timezone.now() + timedelta(minutes=MIN_SCHEDULING_LEAD_MINUTES)
        if scheduled_at < earliest:
            raise forms.ValidationError(
                f"Pick a slot at least {MIN_SCHEDULING_LEAD_MINUTES} minutes from now."
            )
        return scheduled_at


class InterviewFeedbackForm(forms.Form):
    rating = forms.IntegerField(
        label=f"Rating ({RATING_MIN}-{RATING_MAX})", min_value=RATING_MIN, max_value=RATING_MAX
    )
    recommendation = forms.ChoiceField(
        label="Recommendation", choices=Interview.Recommendation.choices
    )
    feedback_comment = forms.CharField(
        label="Comments", widget=forms.Textarea(attrs={"rows": 4}), required=False
    )
