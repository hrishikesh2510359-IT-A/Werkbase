from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.decorators import role_required
from accounts.models import User
from job_postings.models import Application, JobPosting

from .constants import TIER_LABELS
from .services import apply_screening_decision, evaluate_education, evaluate_experience, recompute_for_posting


def _owned_posting_or_403(request, pk):
    """Ownership boundary: a recruiter may only screen candidates for postings
    they personally created - not every posting owned by anyone with the
    HR Recruiter role. Mirrors the check used across job_postings.views."""
    posting = get_object_or_404(JobPosting, pk=pk)
    if posting.posted_by != request.user:
        raise PermissionDenied("You can only screen candidates for your own job postings.")
    return posting


def _owned_application_or_403(request, pk):
    application = get_object_or_404(Application.objects.select_related("job_posting", "candidate"), pk=pk)
    # Ownership boundary: reached through the posting the recruiter owns.
    if application.job_posting.posted_by != request.user:
        raise PermissionDenied("You can only view applicants for your own job postings.")
    return application


# Security boundary: only an HR Recruiter can see ranked candidate lists.
@role_required(User.Role.HR_RECRUITER)
def ranked_candidates_view(request, pk):
    posting = _owned_posting_or_403(request, pk)

    # Names are shown by default so shortlisting is quick; blind review (hide
    # names to reduce first-pass bias) is an explicit, per-request opt-in.
    blind = request.GET.get("blind") == "1"
    status_filter = request.GET.get("status", "")

    applications = (
        posting.applications.select_related("candidate")
        .prefetch_related("interviews__interviewer")  # interview status shown inline
        .order_by(F("match_score").desc(nulls_last=True), "applied_at")
    )

    all_apps = list(applications)
    counts = {
        "all": len(all_apps),
        "new": sum(1 for a in all_apps if a.status == Application.Status.APPLIED),
        "shortlisted": sum(1 for a in all_apps if a.status == Application.Status.SHORTLISTED),
        "rejected": sum(1 for a in all_apps if a.status == Application.Status.REJECTED),
    }
    wanted = {
        "new": (Application.Status.APPLIED,),
        "shortlisted": (Application.Status.SHORTLISTED,),
        "rejected": (Application.Status.REJECTED,),
    }.get(status_filter)
    if wanted:
        all_apps = [a for a in all_apps if a.status in wanted]
    elif status_filter == "progress":
        done = (Application.Status.APPLIED, Application.Status.SHORTLISTED, Application.Status.REJECTED)
        all_apps = [a for a in all_apps if a.status not in done]
    else:
        status_filter = ""
    counts["progress"] = counts["all"] - counts["new"] - counts["shortlisted"] - counts["rejected"]

    rows = []
    for application in all_apps:
        profile = getattr(application.candidate, "candidate_profile", None)
        # Read-only context for the recruiter - scheduling itself is the
        # interviewer's action, handled in the interviews app.
        active_interview = next(
            (i for i in application.interviews.all() if i.status == "SCHEDULED"), None
        )
        # Identity is always shown once a candidate has moved past the blind
        # first pass - blind ranking is a screening aid, not permanent anonymity.
        past_first_pass = application.status != Application.Status.APPLIED
        rows.append({
            "application": application,
            "show_identity": (not blind) or past_first_pass,
            "tier_label": TIER_LABELS.get(application.fit_tier, ""),
            "experience": evaluate_experience(profile, posting) if profile else None,
            "education": evaluate_education(profile, posting) if profile else None,
            "interview": active_interview,
        })

    return render(request, "screening/ranked_candidates.html", {
        "posting": posting,
        "rows": rows,
        "blind": blind,
        "status_filter": status_filter,
        "counts": counts,
        "job_has_no_required_skills": not posting.required_skills.exists(),
    })


@role_required(User.Role.HR_RECRUITER)
def score_card_view(request, pk):
    application = _owned_application_or_403(request, pk)
    posting = application.job_posting
    profile = getattr(application.candidate, "candidate_profile", None)

    return render(request, "screening/score_card.html", {
        "application": application,
        "posting": posting,
        "profile": profile,
        "tier_label": TIER_LABELS.get(application.fit_tier, ""),
        "experience": evaluate_experience(profile, posting) if profile else None,
        "education": evaluate_education(profile, posting) if profile else None,
    })


@role_required(User.Role.HR_RECRUITER)
def recompute_scores_view(request, pk):
    posting = _owned_posting_or_403(request, pk)
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")

    count = recompute_for_posting(posting)
    messages.success(request, f"Recomputed scores for {count} application{'' if count == 1 else 's'}.")
    return redirect("ranked_candidates", pk=posting.pk)


@role_required(User.Role.HR_RECRUITER)
def update_application_status_view(request, pk, action):
    """Manual shortlist/reject. Scoring is advisory only - nothing in this
    module ever changes a status automatically; a recruiter always decides."""
    application = _owned_application_or_403(request, pk)
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")

    transitions = {
        "shortlist": Application.Status.SHORTLISTED,
        "reject": Application.Status.REJECTED,
    }
    if action not in transitions:
        raise PermissionDenied("Unsupported status change.")

    apply_screening_decision(application, action, request.user)
    messages.success(request, f"{application.candidate.full_name} marked {application.get_status_display()}.")

    # The dashboard shortlists inline and wants to land back on the dashboard.
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("ranked_candidates", pk=application.job_posting.pk)
