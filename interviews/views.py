from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User
from job_postings.models import Application

from .constants import FEEDBACK_UNLOCK_MINUTES_AFTER_START
from .forms import InterviewFeedbackForm, InterviewScheduleForm
from .models import Interview, Notification
from .services import (
    cancel_interview,
    find_conflicting_interview,
    google_calendar_enabled,
    reschedule_interview,
    schedule_interview,
    submit_feedback,
)


def _own_interview_or_403(request, pk):
    """Ownership boundary: an interviewer only ever manages interviews they
    themselves scheduled - not every interview held by anyone with the role."""
    interview = get_object_or_404(
        Interview.objects.select_related("application__job_posting", "application__candidate"), pk=pk
    )
    if interview.interviewer != request.user:
        raise PermissionDenied("You can only manage interviews you scheduled.")
    return interview


# ---------------------------------------------------------------------------
# Interviewer — schedule
# ---------------------------------------------------------------------------

# Security boundary: only an Interviewer can schedule interviews.
@role_required(User.Role.INTERVIEWER)
def schedule_interview_view(request, pk):
    application = get_object_or_404(
        Application.objects.select_related("job_posting", "candidate"), pk=pk
    )

    # Scheduling is only ever offered on a Shortlisted application - never on
    # Applied/Rejected/already-scheduled ones.
    if application.status != Application.Status.SHORTLISTED:
        raise PermissionDenied("Only shortlisted candidates can be scheduled for an interview.")

    if request.method == "POST":
        form = InterviewScheduleForm(request.POST)
        if form.is_valid():
            scheduled_at = form.cleaned_data["scheduled_at"]
            duration = form.cleaned_data["duration_minutes"]

            # Double-booking guard, enforced server-side.
            clash = find_conflicting_interview(request.user, scheduled_at, duration)
            if clash:
                form.add_error(
                    "scheduled_at",
                    f"You already have an interview at "
                    f"{timezone.localtime(clash.scheduled_at):%d %b %Y, %I:%M %p}. Pick another slot.",
                )
            else:
                interview = schedule_interview(
                    application=application,
                    interviewer=request.user,  # self-serve; never taken from the form
                    scheduled_at=scheduled_at,
                    duration_minutes=duration,
                    manual_link=form.cleaned_data["manual_link"],
                )
                if interview.is_manual_link and not interview.meet_link:
                    messages.warning(
                        request,
                        "Interview scheduled, but no meeting link was generated or provided. "
                        "Edit the interview to add one.",
                    )
                else:
                    messages.success(request, "Interview scheduled and the candidate was notified.")
                return redirect("my_interviews")
    else:
        form = InterviewScheduleForm()

    return render(request, "interviews/schedule_interview.html", {
        "form": form,
        "application": application,
        "google_enabled": google_calendar_enabled(),
    })


@role_required(User.Role.INTERVIEWER)
def reschedule_interview_view(request, pk):
    interview = _own_interview_or_403(request, pk)
    if interview.status != Interview.Status.SCHEDULED:
        raise PermissionDenied("Only a scheduled interview can be rescheduled.")

    if request.method == "POST":
        form = InterviewScheduleForm(request.POST)
        if form.is_valid():
            scheduled_at = form.cleaned_data["scheduled_at"]
            duration = form.cleaned_data["duration_minutes"]

            # Exclude this interview from its own conflict check.
            clash = find_conflicting_interview(
                request.user, scheduled_at, duration, exclude_pk=interview.pk
            )
            if clash:
                form.add_error(
                    "scheduled_at",
                    f"You already have an interview at "
                    f"{timezone.localtime(clash.scheduled_at):%d %b %Y, %I:%M %p}. Pick another slot.",
                )
            else:
                reschedule_interview(
                    interview,
                    scheduled_at=scheduled_at,
                    duration_minutes=duration,
                    manual_link=form.cleaned_data["manual_link"],
                )
                messages.success(request, "Interview rescheduled and the candidate was notified.")
                return redirect("my_interviews")
    else:
        form = InterviewScheduleForm(initial={
            "scheduled_at": timezone.localtime(interview.scheduled_at).strftime("%Y-%m-%dT%H:%M"),
            "duration_minutes": interview.duration_minutes,
            "manual_link": interview.meet_link,
        })

    return render(request, "interviews/schedule_interview.html", {
        "form": form,
        "application": interview.application,
        "interview": interview,
        "google_enabled": google_calendar_enabled(),
    })


@role_required(User.Role.INTERVIEWER)
def cancel_interview_view(request, pk):
    interview = _own_interview_or_403(request, pk)
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    if interview.status != Interview.Status.SCHEDULED:
        raise PermissionDenied("Only a scheduled interview can be cancelled.")

    cancel_interview(interview)
    messages.success(request, "Interview cancelled. The candidate was notified and remains shortlisted.")
    return redirect("my_interviews")


# ---------------------------------------------------------------------------
# Interviewer — list
# ---------------------------------------------------------------------------

@role_required(User.Role.INTERVIEWER)
def my_interviews_view(request):
    # Ownership boundary: only interviews this interviewer scheduled.
    interviews = Interview.objects.filter(interviewer=request.user).select_related(
        "application__job_posting", "application__candidate"
    )

    scope = request.GET.get("scope", "upcoming")
    now = timezone.now()
    end_of_today = timezone.localtime(now).replace(hour=23, minute=59, second=59)

    if scope == "today":
        visible = [i for i in interviews if i.scheduled_at <= end_of_today and i.status == Interview.Status.SCHEDULED]
    elif scope == "week":
        week_end = now + timedelta(days=7)
        visible = [i for i in interviews if i.scheduled_at <= week_end and i.status == Interview.Status.SCHEDULED]
    elif scope == "all":
        visible = list(interviews)
    else:
        scope = "upcoming"
        visible = [i for i in interviews if i.status == Interview.Status.SCHEDULED]

    rows = [{
        "interview": interview,
        "can_join": interview.is_join_window_open,
        "can_give_feedback": (
            interview.status == Interview.Status.SCHEDULED
            and now >= interview.scheduled_at + timedelta(minutes=FEEDBACK_UNLOCK_MINUTES_AFTER_START)
        ),
    } for interview in visible]

    return render(request, "interviews/my_interviews.html", {"rows": rows, "scope": scope})


# ---------------------------------------------------------------------------
# Interviewer — feedback
# ---------------------------------------------------------------------------

@role_required(User.Role.INTERVIEWER)
def submit_feedback_view(request, pk):
    interview = _own_interview_or_403(request, pk)

    if interview.status == Interview.Status.CANCELLED:
        raise PermissionDenied("This interview was cancelled.")
    if interview.status == Interview.Status.COMPLETED:
        raise PermissionDenied("Feedback has already been submitted for this interview.")
    # Can't file a verdict on a conversation that hasn't started yet.
    unlock_at = interview.scheduled_at + timedelta(minutes=FEEDBACK_UNLOCK_MINUTES_AFTER_START)
    if timezone.now() < unlock_at:
        raise PermissionDenied("Feedback opens once the interview has started.")

    if request.method == "POST":
        form = InterviewFeedbackForm(request.POST)
        if form.is_valid():
            submit_feedback(
                interview,
                rating=form.cleaned_data["rating"],
                comment=form.cleaned_data["feedback_comment"],
                recommendation=form.cleaned_data["recommendation"],
            )
            messages.success(request, "Feedback submitted. The candidate is now marked Interviewed.")
            return redirect("my_interviews")
    else:
        form = InterviewFeedbackForm()

    return render(request, "interviews/submit_feedback.html", {"form": form, "interview": interview})


# ---------------------------------------------------------------------------
# Candidate — notifications
# ---------------------------------------------------------------------------

@role_required(User.Role.CANDIDATE)
def my_notifications_view(request):
    # Ownership boundary: a candidate only ever sees their own notifications.
    notifications = Notification.objects.filter(recipient=request.user)
    # Viewing the page is the read receipt.
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return render(request, "interviews/my_notifications.html", {"notifications": notifications})
