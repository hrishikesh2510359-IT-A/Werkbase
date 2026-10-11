from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.models import User
from job_postings.models import Application

from .services import (
    build_offer_letter_pdf,
    decide_application,
    latest_feedback_interview,
    offer_letter_context,
    send_offer_email,
)


def _rows(applications):
    """Pairs each application with the interview feedback the HR Manager decides on."""
    rows = []
    for app in applications:
        rows.append({"application": app, "interview": latest_feedback_interview(app)})
    return rows


# Security boundary: only an HR Manager makes the final hiring decision.
@role_required(User.Role.HR_MANAGER)
def hiring_decisions_view(request):
    awaiting = (
        Application.objects.filter(status=Application.Status.INTERVIEWED)
        .select_related("candidate", "job_posting")
        .prefetch_related("interviews")
    )
    decided = (
        Application.objects.filter(status__in=[Application.Status.SELECTED, Application.Status.REJECTED],
                                   decided_at__isnull=False)
        .select_related("candidate", "job_posting")
        .order_by("-decided_at")[:10]
    )
    return render(request, "hiring/decisions.html", {
        "awaiting_rows": _rows(awaiting),
        "decided": decided,
    })


@role_required(User.Role.HR_MANAGER)
def decide_view(request, pk, action):
    application = get_object_or_404(
        Application.objects.select_related("candidate", "job_posting"), pk=pk
    )
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    # Only an interviewed candidate can receive a final decision.
    if application.status != Application.Status.INTERVIEWED:
        raise PermissionDenied("A final decision can only be made on an interviewed candidate.")
    if action not in ("select", "reject"):
        raise PermissionDenied("Unsupported decision.")

    selected = action == "select"
    offer_ctc, joining_date = "", None
    if selected:
        # A selection produces an offer letter, so its terms are mandatory.
        offer_ctc = request.POST.get("offer_ctc", "").strip()[:100]
        try:
            joining_date = date.fromisoformat(request.POST.get("joining_date", "").strip())
        except ValueError:
            joining_date = None
        if not offer_ctc or joining_date is None:
            messages.error(request, "To select a candidate, enter the annual CTC and the joining date for the offer letter.")
            return redirect("hiring_decisions")
        if joining_date < date.today():
            messages.error(request, "The joining date can't be in the past.")
            return redirect("hiring_decisions")

    decide_application(
        application,
        decided_by=request.user,
        selected=selected,
        note=request.POST.get("note", "").strip(),
        offer_ctc=offer_ctc,
        joining_date=joining_date,
    )
    messages.success(request, f"{application.candidate.full_name} marked {application.get_status_display()}.")
    return redirect("hiring_decisions")


# ---------------------------------------------------------------------------
# Offer letter
# ---------------------------------------------------------------------------

def _offer_application_or_404(request, pk):
    """Ownership boundary: only the selected candidate themself, or an HR
    Manager, may open an offer letter - and only once it actually exists."""
    if not request.user.is_authenticated:
        raise PermissionDenied
    application = get_object_or_404(
        Application.objects.select_related("candidate", "job_posting", "decided_by"), pk=pk
    )
    is_owner = application.candidate_id == request.user.pk
    if not (is_owner or request.user.role == User.Role.HR_MANAGER):
        raise PermissionDenied("You can't view this offer letter.")
    if application.status != Application.Status.SELECTED:
        raise Http404("No offer letter for this application.")
    return application


def offer_letter_view(request, pk):
    application = _offer_application_or_404(request, pk)
    letter = offer_letter_context(application)
    joining = letter["joining_date"].strftime("%d %B %Y") if letter["joining_date"] else "To be confirmed"
    terms = [
        ("Position", letter["job_title"]),
        ("Department", letter["department"]),
        ("Employment type", letter["employment_type"]),
        ("Work mode / Location", f"{letter['work_mode']} - {letter['location']}"),
        ("Annual compensation (CTC)", letter["ctc"] or "As discussed"),
        ("Date of joining", joining),
    ]
    return render(request, "hiring/offer_letter.html", {
        "letter": letter,
        "terms": terms,
        "application": application,
        "active_nav": "applications" if application.candidate_id == request.user.pk else "hiring_decisions",
    })


def offer_letter_pdf_view(request, pk):
    application = _offer_application_or_404(request, pk)
    response = HttpResponse(build_offer_letter_pdf(application), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Offer_Letter_{application.offer_reference}.pdf"'
    return response


def celebration_seen_view(request, pk):
    """Records that the candidate dismissed the congratulations pop-up.
    Ownership boundary: only the selected candidate can mark their own."""
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    application = get_object_or_404(Application, pk=pk, candidate=request.user)
    Application.objects.filter(pk=application.pk).update(congrats_seen=True)
    return HttpResponse(status=204)


@role_required(User.Role.HR_MANAGER)
def resend_offer_email_view(request, pk):
    """Re-sends the offer email, e.g. after a mail outage or a corrected address."""
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    application = get_object_or_404(
        Application.objects.select_related("candidate", "job_posting", "decided_by"),
        pk=pk, status=Application.Status.SELECTED,
    )
    if send_offer_email(application):
        messages.success(request, f"Offer letter emailed to {application.candidate.email}.")
    else:
        messages.error(request, "The email couldn't be sent. Check the email settings and try again.")
    return redirect("hiring_decisions")
