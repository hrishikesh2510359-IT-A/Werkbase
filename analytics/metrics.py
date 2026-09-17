"""
Read-only aggregations for the recruitment analytics dashboard.

Every metric is its own function taking the same filter arguments, so each can
be tested in isolation. All counting happens in the database via the Django ORM
(annotate / aggregate / Count / Avg) - no raw SQL, and no per-row Python loops
over querysets, so the page stays inside the 3-second response budget.
"""

from django.db.models import (
    Avg,
    Count,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
)

from interviews.models import Interview
from job_postings.models import REFERRAL_SOURCE_CHOICES, Application, JobPosting

S = Application.Status

# --- "Selected" definition -------------------------------------------------
# ASSUMPTION: Application.Status.SELECTED exists as a choice, but no module
# sets it yet - final-hire approval is a later module. Counting SELECTED alone
# would pin "Selected" at 0 forever. So an application counts as selected if
# EITHER its status is SELECTED (the real signal, once that module ships) OR it
# is INTERVIEWED with a completed interview recommending PROCEED (the proxy).
# The two branches are mutually exclusive by status, so nothing is counted twice,
# and the proxy retires naturally as real SELECTED statuses start appearing.
SELECTED_Q = Q(status=S.SELECTED) | Q(status=S.INTERVIEWED, proceeded=True)


def _proceed_interviews():
    return Interview.objects.filter(
        application=OuterRef("pk"),
        status=Interview.Status.COMPLETED,
        recommendation=Interview.Recommendation.PROCEED,
    )


def filtered_applications(job_id=None, date_from=None, date_to=None):
    """The single filtered application queryset every metric builds on.

    Date filters use the APPLIED date: every metric on this dashboard is about
    candidates moving through a pipeline, so "applications received in this
    window" is the question the filter should answer.
    """
    qs = Application.objects.annotate(
        # Exists subquery rather than a join, so an application with several
        # interviews can't be counted more than once by the aggregates below.
        proceeded=Exists(_proceed_interviews()),
    )
    if job_id:
        qs = qs.filter(job_posting_id=job_id)
    if date_from:
        qs = qs.filter(applied_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(applied_at__date__lte=date_to)
    return qs


# ---------------------------------------------------------------------------
# Piece 4 - summary cards
# ---------------------------------------------------------------------------

def summary_counts(job_id=None, date_from=None, date_to=None):
    applications = filtered_applications(job_id, date_from, date_to)

    # Postings use their own created date for the date filter - "postings in this
    # window" - since a posting has no applied date of its own.
    postings = JobPosting.objects.all()
    if job_id:
        postings = postings.filter(pk=job_id)
    if date_from:
        postings = postings.filter(created_at__date__gte=date_from)
    if date_to:
        postings = postings.filter(created_at__date__lte=date_to)

    counts = applications.aggregate(
        total_applications=Count("pk"),
        total_selected=Count("pk", filter=SELECTED_Q),
    )

    # Every interview that was actually booked for these applications. Cancelled
    # ones are excluded: a cancelled slot never turned into an interview.
    counts["total_interviews"] = (
        Interview.objects.filter(application__in=applications.values("pk"))
        .exclude(status=Interview.Status.CANCELLED)
        .count()
    )
    counts["total_postings"] = postings.count()
    return counts


# ---------------------------------------------------------------------------
# Piece 1 - application funnel
# ---------------------------------------------------------------------------

FUNNEL_STAGES = ["Applied", "Shortlisted", "Interview Scheduled", "Interviewed", "Selected"]


def application_funnel(job_id=None, date_from=None, date_to=None):
    """Cumulative "reached at least this stage" counts.

    Deliberate choice: a raw count of each *current* status isn't a funnel - a
    candidate who has been interviewed is no longer "shortlisted", so a snapshot
    would show fewer shortlisted than interviewed and read as nonsense. Instead
    each bar counts everyone who got that far.

    ASSUMPTION: REJECTED only counts toward "Applied". There's no status history,
    so we can't tell whether a rejection happened before or after shortlisting.
    """
    applications = filtered_applications(job_id, date_from, date_to)

    reached_shortlist = Q(status__in=[S.SHORTLISTED, S.INTERVIEW_SCHEDULED, S.INTERVIEWED, S.SELECTED])
    reached_scheduled = Q(status__in=[S.INTERVIEW_SCHEDULED, S.INTERVIEWED, S.SELECTED])
    reached_interviewed = Q(status__in=[S.INTERVIEWED, S.SELECTED])

    counts = applications.aggregate(
        applied=Count("pk"),
        shortlisted=Count("pk", filter=reached_shortlist),
        scheduled=Count("pk", filter=reached_scheduled),
        interviewed=Count("pk", filter=reached_interviewed),
        selected=Count("pk", filter=SELECTED_Q),
    )
    values = [counts["applied"], counts["shortlisted"], counts["scheduled"],
              counts["interviewed"], counts["selected"]]
    return {"labels": FUNNEL_STAGES, "values": values}


# ---------------------------------------------------------------------------
# Piece 2 - time to hire
# ---------------------------------------------------------------------------

def time_to_hire(job_id=None, date_from=None, date_to=None, sort="desc"):
    """Average days from a job going live to a positive hiring outcome, per job.

    ASSUMPTIONS:
      * Start = JobPosting.created_at. There's no published_at field, so this can
        overstate the figure for a posting that sat in Draft before publishing.
      * End = when the interviewer recorded a PROCEED recommendation. Applications
        have no status-change timestamp, so this is the only dated positive signal.
    Jobs with no hire yet produce no rows at all, so they're excluded from the
    averages rather than dragging them down as zeroes.
    """
    hire_at = Subquery(
        _proceed_interviews().order_by("feedback_submitted_at").values("feedback_submitted_at")[:1]
    )

    rows = (
        filtered_applications(job_id, date_from, date_to)
        .filter(SELECTED_Q)
        .annotate(hire_at=hire_at)
        .filter(hire_at__isnull=False)
        .values("job_posting_id", "job_posting__job_title")
        .annotate(
            avg_duration=Avg(
                ExpressionWrapper(F("hire_at") - F("job_posting__created_at"), output_field=DurationField())
            ),
            hires=Count("pk"),
        )
    )

    results = []
    for row in rows:
        days = row["avg_duration"].total_seconds() / 86400
        results.append({
            "job_title": row["job_posting__job_title"],
            "avg_days": round(max(days, 0), 1),
            "hires": row["hires"],
        })

    results.sort(key=lambda r: r["avg_days"], reverse=(sort != "asc"))
    return results


# ---------------------------------------------------------------------------
# Piece 3 - source tracking
# ---------------------------------------------------------------------------

def source_breakdown(job_id=None, date_from=None, date_to=None):
    """Applications grouped by how the candidate found the job.

    Reuses the existing Application.referral_source field - it was already added
    by the application-form revamp, so no schema change was needed here.
    Applications submitted before that field existed have a blank source and are
    shown honestly as "Not recorded" rather than dropped.
    """
    labels_by_key = dict(REFERRAL_SOURCE_CHOICES)
    rows = (
        filtered_applications(job_id, date_from, date_to)
        .values("referral_source")
        .annotate(count=Count("pk"))
        .order_by("-count")
    )
    return {
        "labels": [labels_by_key.get(r["referral_source"], "Not recorded") for r in rows],
        "values": [r["count"] for r in rows],
    }
