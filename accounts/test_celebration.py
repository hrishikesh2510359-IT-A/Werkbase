"""Selected-job hiding in Browse, the one-time congratulations pop-up, and the
dark-mode toggle being present on every page shell."""

from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from job_postings.models import Application, JobPosting

from .models import User

PWD = "Test@1234"


def make(email, role, name="Cara Candidate"):
    return User.objects.create_user(email=email, password=PWD, full_name=name, role=role)


class SelectedJobsAndCelebrationTests(TestCase):
    def setUp(self):
        self.rec = make("rec@x.com", User.Role.HR_RECRUITER, "Rec")
        self.me = make("me@x.com", User.Role.CANDIDATE, "Cara Candidate")
        self.other = make("other@x.com", User.Role.CANDIDATE, "Other Person")

        def posting(title):
            return JobPosting.objects.create(
                job_title=title, department="ENGINEERING", employment_type="FULL_TIME", work_mode="ON_SITE",
                num_openings=1, location="X", status=JobPosting.Status.PUBLISHED, posted_by=self.rec)

        self.won = posting("Won Role")
        self.pending = posting("Pending Role")
        self.untouched = posting("Untouched Role")

        def apply(candidate, job, status):
            return Application.objects.create(job_posting=job, candidate=candidate, status=status,
                                              resume=SimpleUploadedFile("r.txt", b"x"),
                                              decided_at=timezone.now(), offer_ctc="8L",
                                              offer_joining_date=timezone.localdate() + timedelta(days=9))

        self.won_app = apply(self.me, self.won, Application.Status.SELECTED)
        apply(self.me, self.pending, Application.Status.APPLIED)
        # Someone ELSE being selected for a job must not hide it from me.
        apply(self.other, self.untouched, Application.Status.SELECTED)
        self.client.login(email="me@x.com", password=PWD)

    # --- bug: selected role is not browsable -------------------------------
    def test_browse_hides_only_the_role_i_was_selected_for(self):
        page = self.client.get(reverse("browse_jobs"))
        self.assertNotContains(page, "Won Role")
        self.assertContains(page, "Pending Role")       # applied but undecided: still listed
        self.assertContains(page, "Untouched Role")     # another candidate's win is irrelevant

    def test_dashboard_latest_openings_also_hides_it(self):
        # (the role still appears under *My Applications* - only the openings list excludes it)
        titles = [j.job_title for j in self.client.get(reverse("dashboard")).context["recent_jobs"]]
        self.assertNotIn("Won Role", titles)
        self.assertIn("Untouched Role", titles)

    def test_search_cannot_bring_it_back(self):
        self.assertNotContains(self.client.get(reverse("browse_jobs"), {"q": "Won"}), "Won Role")

    # --- celebration --------------------------------------------------------
    def test_poll_reports_celebration_until_dismissed(self):
        data = self.client.get(reverse("notifications_poll")).json()
        self.assertEqual(data["celebrate"]["job_title"], "Won Role")
        self.assertEqual(data["celebrate"]["name"], "Cara")
        self.assertEqual(data["celebrate"]["offer_url"], reverse("offer_letter", args=[self.won_app.pk]))
        # Not dismissed yet -> still pending on the next poll / page.
        self.assertIsNotNone(self.client.get(reverse("notifications_poll")).json()["celebrate"])

        response = self.client.post(reverse("celebration_seen", args=[self.won_app.pk]))
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.client.get(reverse("notifications_poll")).json()["celebrate"])

    def test_only_selected_unseen_applications_celebrate(self):
        self.won_app.status = Application.Status.REJECTED
        self.won_app.save()
        self.assertIsNone(self.client.get(reverse("notifications_poll")).json()["celebrate"])

    def test_cannot_dismiss_someone_elses_celebration(self):
        self.client.logout()
        self.client.login(email="rec@x.com", password=PWD)
        self.assertEqual(self.client.post(reverse("celebration_seen", args=[self.won_app.pk])).status_code, 404)
        self.won_app.refresh_from_db()
        self.assertFalse(self.won_app.congrats_seen)

    def test_dismiss_requires_post_and_login(self):
        self.assertEqual(self.client.get(reverse("celebration_seen", args=[self.won_app.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("celebration_seen", args=[self.won_app.pk])).status_code, 403)

    def test_popup_markup_ships_on_app_pages(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "showCelebration")
        self.assertContains(page, "View Offer Letter")

    # --- dark mode ------------------------------------------------------------
    def test_dark_mode_toggle_on_app_shell(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "wbToggleTheme()")
        self.assertContains(page, "data-theme-icon")
        self.assertContains(page, "--c-background: 15 26 32")   # the dark palette is defined

    def test_dark_mode_toggle_on_public_pages(self):
        self.client.logout()
        for name in ("portal_select", "candidate_login", "company_login"):
            self.assertContains(self.client.get(reverse(name)), "wbToggleTheme()", msg_prefix=name)
