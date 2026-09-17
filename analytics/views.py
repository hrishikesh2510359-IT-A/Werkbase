from datetime import date

from django.shortcuts import render

from accounts.decorators import role_required
from accounts.models import User
from job_postings.models import JobPosting

from . import metrics

# Every company-side role may view analytics; Candidate is deliberately absent.
HR_ROLES = (
    User.Role.HIRING_MANAGER,
    User.Role.HR_RECRUITER,
    User.Role.INTERVIEWER,
    User.Role.HR_MANAGER,
    User.Role.ONBOARDING_COORDINATOR,
    User.Role.HR_ADMIN,
)


def _parse_date(value):
    """ISO date from a query param, or None for anything blank/malformed -
    a bad date in the URL should drop that filter, not crash the page."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_int(value):
    try:
        return int(value) if value else None
    except ValueError:
        return None


# Security boundary: HR sub-roles only, enforced server-side. Candidates get a
# 403 from role_required no matter how they reach this URL.
@role_required(*HR_ROLES)
def dashboard_view(request):
    job_id = _parse_int(request.GET.get("job"))
    date_from = _parse_date(request.GET.get("date_from"))
    date_to = _parse_date(request.GET.get("date_to"))
    sort = "asc" if request.GET.get("sort") == "asc" else "desc"

    filters = {"job_id": job_id, "date_from": date_from, "date_to": date_to}

    funnel = metrics.application_funnel(**filters)
    sources = metrics.source_breakdown(**filters)

    return render(request, "analytics/dashboard.html", {
        "summary": metrics.summary_counts(**filters),
        "funnel": funnel,
        "funnel_has_data": any(funnel["values"]),
        "time_to_hire": metrics.time_to_hire(**filters, sort=sort),
        "sources": sources,
        "sources_has_data": bool(sources["values"]),
        # Published and Closed: closed jobs are exactly where completed hires
        # live, so leaving them out would hide the most useful history.
        "job_choices": JobPosting.objects.filter(
            status__in=[JobPosting.Status.PUBLISHED, JobPosting.Status.CLOSED]
        ).order_by("job_title").values("id", "job_title"),
        "selected_job": job_id,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "sort": sort,
        "filters_active": bool(job_id or date_from or date_to),
    })
