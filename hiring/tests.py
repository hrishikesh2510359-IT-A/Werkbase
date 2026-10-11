"""Hiring-decision and offer-letter tests: selecting requires offer terms,
the letter is visible only to its candidate and HR Managers, rejection makes
no letter, and the PDF is real."""

from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from interviews.models import Notification
from job_postings.models import Application, JobPosting

PWD = "Test@1234"


def make_user(email, role, name):
    return User.objects.create_user(email=email, password=PWD, full_name=name, role=role)


class OfferLetterTests(TestCase):
    def setUp(self):
        self.manager = make_user("hm@example.com", User.Role.HR_MANAGER, "Hema Manager")
        self.recruiter = make_user("rec@example.com", User.Role.HR_RECRUITER, "Rec")
        self.candidate = make_user("c@example.com", User.Role.CANDIDATE, "Cand One")
        self.other = make_user("o@example.com", User.Role.CANDIDATE, "Cand Two")
        posting = JobPosting.objects.create(
            job_title="Backend Engineer", department="ENGINEERING", employment_type="FULL_TIME",
            work_mode="ON_SITE", num_openings=1, location="Chennai",
            status=JobPosting.Status.PUBLISHED, posted_by=self.recruiter,
        )
        self.app = Application.objects.create(
            job_posting=posting, candidate=self.candidate,
            resume=SimpleUploadedFile("r.txt", b"cv"), status=Application.Status.INTERVIEWED,
        )
        self.joining = (timezone.localdate() + timedelta(days=30)).isoformat()

    def _select(self, **post):
        self.client.login(email="hm@example.com", password=PWD)
        return self.client.post(reverse("decide_application", args=[self.app.pk, "select"]), post)

    def _select_ok(self):
        return self._select(offer_ctc="8,00,000 per annum", joining_date=self.joining, note="Great")

    # --- deciding -----------------------------------------------------------
    def test_select_requires_offer_terms(self):
        self._select(note="x")
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.INTERVIEWED)  # unchanged

    def test_past_joining_date_rejected(self):
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        self._select(offer_ctc="8L", joining_date=past)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.INTERVIEWED)

    def test_select_stores_terms_and_notifies_with_letter_link(self):
        self._select_ok()
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.SELECTED)
        self.assertEqual(self.app.offer_ctc, "8,00,000 per annum")
        note = Notification.objects.get(recipient=self.candidate)
        self.assertEqual(note.link, f"/hiring/offer/{self.app.pk}/")
        self.assertIn("offer letter", note.message.lower())

    def test_reject_needs_no_terms_and_makes_no_letter(self):
        self.client.login(email="hm@example.com", password=PWD)
        self.client.post(reverse("decide_application", args=[self.app.pk, "reject"]))
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.REJECTED)
        self.client.logout()
        self.client.login(email="c@example.com", password=PWD)
        self.assertEqual(self.client.get(reverse("offer_letter", args=[self.app.pk])).status_code, 404)

    # --- viewing ------------------------------------------------------------
    def test_candidate_sees_own_letter_with_terms(self):
        self._select_ok()
        self.client.logout()
        self.client.login(email="c@example.com", password=PWD)
        response = self.client.get(reverse("offer_letter", args=[self.app.pk]))
        self.assertEqual(response.status_code, 200)
        for text in ("Cand One", "Backend Engineer", "8,00,000 per annum", "Hema Manager", self.app.offer_reference):
            self.assertContains(response, text)

    def test_other_candidate_and_recruiter_are_blocked(self):
        self._select_ok()
        self.client.logout()
        for email in ("o@example.com", "rec@example.com"):
            self.client.login(email=email, password=PWD)
            self.assertEqual(self.client.get(reverse("offer_letter", args=[self.app.pk])).status_code, 403, email)
            self.assertEqual(self.client.get(reverse("offer_letter_pdf", args=[self.app.pk])).status_code, 403, email)
            self.client.logout()

    def test_anonymous_is_blocked(self):
        self._select_ok()
        self.client.logout()
        self.assertEqual(self.client.get(reverse("offer_letter", args=[self.app.pk])).status_code, 403)

    def test_pdf_download_is_a_real_pdf(self):
        self._select_ok()
        self.client.logout()
        self.client.login(email="c@example.com", password=PWD)
        response = self.client.get(reverse("offer_letter_pdf", args=[self.app.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(self.app.offer_reference, response["Content-Disposition"])

    def test_candidate_applications_page_links_to_letter(self):
        self._select_ok()
        self.client.logout()
        self.client.login(email="c@example.com", password=PWD)
        response = self.client.get(reverse("my_applications"))
        self.assertContains(response, reverse("offer_letter", args=[self.app.pk]))


class OfferEmailTests(OfferLetterTests):
    """Selecting a candidate emails the offer letter (PDF attached). Django's
    test runner swaps in an in-memory mailbox, so nothing is ever really sent."""

    def test_selection_emails_the_candidate_with_the_pdf(self):
        from django.core import mail
        self._select_ok()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["c@example.com"])
        self.assertIn("Backend Engineer", message.subject)
        self.assertIn("8,00,000 per annum", message.body)
        self.assertIn(f"/hiring/offer/{self.app.pk}/", message.body)
        name, content, mimetype = message.attachments[0]
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertIn(self.app.offer_reference, name)
        self.app.refresh_from_db()
        self.assertIsNotNone(self.app.offer_email_sent_at)

    def test_rejection_sends_no_email(self):
        from django.core import mail
        self.client.login(email="hm@example.com", password=PWD)
        self.client.post(reverse("decide_application", args=[self.app.pk, "reject"]))
        self.assertEqual(len(mail.outbox), 0)

    def test_mail_failure_never_blocks_the_decision(self):
        from unittest import mock
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            self._select_ok()
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.SELECTED)   # decision stands
        self.assertIsNone(self.app.offer_email_sent_at)                  # but flagged as not emailed
        self.assertTrue(Notification.objects.filter(recipient=self.candidate).exists())  # portal notice still sent

    def test_hr_manager_can_resend_after_a_failure(self):
        from django.core import mail
        from unittest import mock
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            self._select_ok()
        self.assertEqual(len(mail.outbox), 0)
        page = self.client.get(reverse("hiring_decisions"))
        self.assertContains(page, "Send email")
        self.client.post(reverse("resend_offer_email", args=[self.app.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.app.refresh_from_db()
        self.assertIsNotNone(self.app.offer_email_sent_at)
        self.assertContains(self.client.get(reverse("hiring_decisions")), "Emailed")

    def test_only_hr_manager_can_resend(self):
        self._select_ok()
        self.client.logout()
        for email in ("c@example.com", "rec@example.com"):
            self.client.login(email=email, password=PWD)
            self.assertEqual(self.client.post(reverse("resend_offer_email", args=[self.app.pk])).status_code, 403, email)
            self.client.logout()


class ConsoleBackendIsNotDeliveryTests(OfferLetterTests):
    def test_console_backend_never_claims_emailed(self):
        from django.test import override_settings
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            self._select_ok()
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.SELECTED)
        self.assertIsNone(self.app.offer_email_sent_at)   # printed to a log, not delivered
