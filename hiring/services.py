"""
Final hiring decision (HR Manager) and the offer letter that follows a
selection. Isolated from the views so the transition is reusable and
testable. Reuses the interviews notification channel to tell the candidate
the outcome.
"""

import io
from xml.sax.saxutils import escape

from django.utils import timezone

from interviews.services import notify, notify_application_staff
from job_postings.models import Application

COMPANY_NAME = "Werkbase"


def latest_feedback_interview(application):
    """The most recent completed interview for this application - the one whose
    feedback the HR Manager is deciding on."""
    return (
        application.interviews.filter(status="COMPLETED")
        .order_by("-feedback_submitted_at")
        .first()
    )


def decide_application(application, *, decided_by, selected, note="", offer_ctc="", joining_date=None):
    """Records an HR Manager's final decision on an INTERVIEWED candidate.

    selected=True  -> Application.Status.SELECTED (+ offer terms for the letter)
    selected=False -> Application.Status.REJECTED
    Stores who/when/why and notifies the candidate.
    """
    application.status = Application.Status.SELECTED if selected else Application.Status.REJECTED
    application.decided_by = decided_by
    application.decided_at = timezone.now()
    application.decision_note = note
    fields = ["status", "decided_by", "decided_at", "decision_note"]
    if selected:
        application.offer_ctc = offer_ctc
        application.offer_joining_date = joining_date
        fields += ["offer_ctc", "offer_joining_date"]
    application.save(update_fields=fields)

    title = application.job_posting.job_title
    name = application.candidate.full_name
    notify_application_staff(
        application,
        f"Hiring decision: {name} was {'selected' if selected else 'not selected'} for {title}"
        f"{' - offer letter sent.' if selected else '.'}",
        actor=decided_by, include_interviewers=True,
    )
    if selected:
        notify(
            application.candidate,
            f"Congratulations! You have been selected for {title}. Your offer letter is ready.",
            link=f"/hiring/offer/{application.pk}/",
        )
        # Email is best-effort: the decision is already saved, so a mail outage
        # must never undo or block it (HR can re-send from the Decisions page).
        send_offer_email(application)
    else:
        notify(
            application.candidate,
            f"Update on your application: you have not been selected for {title}.",
            link="/jobs/applications/mine/",
        )
    return application


# ---------------------------------------------------------------------------
# Offer letter
# ---------------------------------------------------------------------------

def offer_letter_context(application):
    """Single source of truth for the letter, used by both the on-screen page
    and the PDF so they can never disagree."""
    posting = application.job_posting
    signer = application.decided_by
    return {
        "company": COMPANY_NAME,
        "reference": application.offer_reference,
        "issued_on": timezone.localtime(application.decided_at).date() if application.decided_at else timezone.localdate(),
        "candidate_name": application.candidate.full_name,
        "job_title": posting.job_title,
        "department": posting.get_department_display(),
        "employment_type": posting.get_employment_type_display(),
        "work_mode": posting.get_work_mode_display(),
        "location": posting.location,
        "ctc": application.offer_ctc,
        "joining_date": application.offer_joining_date,
        "signer_name": signer.full_name if signer else "HR Manager",
        "signer_title": "HR Manager",
    }


def send_offer_email(application):
    """Emails the offer letter (PDF attached) to the candidate.

    Returns True if the mail server accepted it, False otherwise (never raises).
    On success the send time is stored so HR can see it was delivered."""
    import logging
    from django.conf import settings
    from django.core.mail import EmailMessage

    c = offer_letter_context(application)
    joining = c["joining_date"].strftime("%d %B %Y") if c["joining_date"] else "to be confirmed"
    body = (
        f"Dear {c['candidate_name']},\n\n"
        f"Congratulations! We are delighted to offer you the position of {c['job_title']} at {c['company']}.\n\n"
        f"  Position:       {c['job_title']}\n"
        f"  Compensation:   {c['ctc'] or 'As discussed'}\n"
        f"  Date of joining: {joining}\n\n"
        f"Your offer letter is attached as a PDF. You can also view it any time in the portal:\n"
        f"{settings.SITE_URL}/hiring/offer/{application.pk}/\n\n"
        f"We look forward to welcoming you.\n\n"
        f"{c['signer_name']}\n{c['signer_title']}, {c['company']}\n"
    )
    try:
        message = EmailMessage(
            subject=f"Your offer letter from {c['company']} - {c['job_title']}",
            body=body,
            to=[application.candidate.email],
        )
        message.attach(f"Offer_Letter_{c['reference']}.pdf", build_offer_letter_pdf(application), "application/pdf")
        message.send(fail_silently=False)
    except Exception:
        logging.getLogger(__name__).exception("Offer email to %s failed", application.candidate.email)
        return False
    if "console" in settings.EMAIL_BACKEND or "dummy" in settings.EMAIL_BACKEND:
        # The console backend only prints the message in the server log. That is
        # not delivery, so never claim "Emailed" - the Send email button stays.
        logging.getLogger(__name__).warning(
            "Offer email to %s was NOT delivered: no mail server configured (EMAIL_HOST_USER/PASSWORD).",
            application.candidate.email)
        return False
    application.offer_email_sent_at = timezone.now()
    application.save(update_fields=["offer_email_sent_at"])
    return True


def _pdf_text(value):
    # reportlab's built-in Helvetica has no rupee glyph; avoid a blank box.
    return escape(str(value)).replace("₹", "Rs. ")


def build_offer_letter_pdf(application):
    """Renders the offer letter to PDF bytes (reportlab - free, offline)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    c = offer_letter_context(application)
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=10.5, leading=16)
    small = ParagraphStyle("small", parent=body, fontSize=9, textColor=colors.HexColor("#5a6b73"))
    brand = ParagraphStyle("brand", parent=body, fontName="Helvetica-Bold", fontSize=22, textColor=colors.HexColor("#2C5F7C"), leading=26)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=13, spaceBefore=6, spaceAfter=8)

    joining = c["joining_date"].strftime("%d %B %Y") if c["joining_date"] else "To be confirmed"
    terms = Table(
        [
            ["Position", _pdf_text(c["job_title"])],
            ["Department", _pdf_text(c["department"])],
            ["Employment type", _pdf_text(c["employment_type"])],
            ["Work mode / Location", _pdf_text(f"{c['work_mode']} - {c['location']}")],
            ["Annual compensation (CTC)", _pdf_text(c["ctc"] or "As discussed")],
            ["Date of joining", _pdf_text(joining)],
        ],
        colWidths=[60 * mm, 100 * mm],
    )
    terms.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8f8")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe4e6")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story = [
        Paragraph(_pdf_text(c["company"]), brand),
        Paragraph("Recruitment &amp; Talent Acquisition", small),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#5f7d4f")),
        Spacer(1, 10),
        Paragraph(f"Ref: {_pdf_text(c['reference'])} &nbsp;&nbsp;|&nbsp;&nbsp; Date: {c['issued_on']:%d %B %Y}", small),
        Spacer(1, 14),
        Paragraph("OFFER OF EMPLOYMENT", title),
        Paragraph(f"Dear {_pdf_text(c['candidate_name'])},", body),
        Spacer(1, 6),
        Paragraph(
            f"We are delighted to offer you the position of <b>{_pdf_text(c['job_title'])}</b> at "
            f"{_pdf_text(c['company'])}. Following your interview, we were impressed by your skills and "
            "experience and believe you will be a valuable member of our team. The terms of your offer are below.",
            body,
        ),
        Spacer(1, 10),
        terms,
        Spacer(1, 12),
        Paragraph(
            "This offer is subject to satisfactory verification of your documents and references. "
            "Please report on the date of joining mentioned above; our team will contact you through the portal "
            "with onboarding details.",
            body,
        ),
        Spacer(1, 10),
        Paragraph("We look forward to welcoming you.", body),
        Spacer(1, 26),
        Paragraph("Sincerely,", body),
        Spacer(1, 22),
        Paragraph(f"<b>{_pdf_text(c['signer_name'])}</b>", body),
        Paragraph(f"{_pdf_text(c['signer_title'])}, {_pdf_text(c['company'])}", small),
    ]

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=25 * mm, rightMargin=25 * mm, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"Offer Letter - {c['candidate_name']}", author=c["company"],
    ).build(story)
    return buffer.getvalue()
