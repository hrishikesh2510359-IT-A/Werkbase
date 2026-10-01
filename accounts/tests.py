"""Auth & access-control tests: login success/failure and role-gated views.

Only roles that actually have views are exercised - HR Manager / Onboarding
Coordinator have no screens yet, so there's nothing of theirs to test beyond
the shared dashboard, which every role reaches.
"""

from django.test import TestCase
from django.urls import reverse

from .models import User

PWD = "Test@1234"


class LoginTests(TestCase):
    def setUp(self):
        self.candidate = User.objects.create_user(
            email="cand@example.com", password=PWD, full_name="Cand", role=User.Role.CANDIDATE
        )
        self.recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )

    def test_candidate_login_success(self):
        resp = self.client.post(
            reverse("candidate_login"), {"email": "cand@example.com", "password": PWD}
        )
        self.assertRedirects(resp, reverse("dashboard"))

    def test_login_wrong_password_fails(self):
        resp = self.client.post(
            reverse("candidate_login"), {"email": "cand@example.com", "password": "wrong"}
        )
        self.assertContains(resp, "Invalid email or password")

    def test_company_portal_rejects_candidate(self):
        # A candidate's valid credentials must not work on the company portal.
        resp = self.client.post(
            reverse("company_login"), {"email": "cand@example.com", "password": PWD}
        )
        self.assertContains(resp, "Invalid email or password")

    def test_candidate_portal_rejects_company_user(self):
        resp = self.client.post(
            reverse("candidate_login"), {"email": "rec@example.com", "password": PWD}
        )
        self.assertContains(resp, "Invalid email or password")


class RoleGuardTests(TestCase):
    def setUp(self):
        self.candidate = User.objects.create_user(
            email="cand@example.com", password=PWD, full_name="Cand", role=User.Role.CANDIDATE
        )
        self.recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PWD, full_name="Admin", role=User.Role.HR_ADMIN
        )

    def test_candidate_denied_analytics(self):
        self.client.force_login(self.candidate)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 403)

    def test_hr_role_allowed_analytics(self):
        self.client.force_login(self.recruiter)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 200)

    def test_non_admin_denied_create_user(self):
        self.client.force_login(self.recruiter)
        self.assertEqual(self.client.get(reverse("create_company_user")).status_code, 403)

    def test_admin_allowed_create_user(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("create_company_user")).status_code, 200)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("analytics_dashboard"))
        self.assertEqual(resp.status_code, 302)


class InterviewerDashboardRegressionTests(TestCase):
    """Regression for the 'Awaiting Review' dead end: the interviewer's actionable
    list must surface SHORTLISTED candidates (schedulable), not APPLIED ones."""

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from job_postings.models import Application, JobPosting

        self.interviewer = User.objects.create_user(
            email="iv@example.com", password=PWD, full_name="Ivy", role=User.Role.INTERVIEWER
        )
        recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        posting = JobPosting.objects.create(
            job_title="Backend", department="ENGINEERING", employment_type="FULL_TIME",
            work_mode="ON_SITE", num_openings=1, location="Chennai",
            status=JobPosting.Status.PUBLISHED, posted_by=recruiter,
        )
        applied_cand = User.objects.create_user(
            email="a@example.com", password=PWD, full_name="Applied Cand", role=User.Role.CANDIDATE
        )
        short_cand = User.objects.create_user(
            email="s@example.com", password=PWD, full_name="Short Cand", role=User.Role.CANDIDATE
        )
        Application.objects.create(
            job_posting=posting, candidate=applied_cand,
            resume=SimpleUploadedFile("r.txt", b"x"), status=Application.Status.APPLIED,
        )
        Application.objects.create(
            job_posting=posting, candidate=short_cand,
            resume=SimpleUploadedFile("r.txt", b"x"), status=Application.Status.SHORTLISTED,
        )

    def test_awaiting_list_shows_shortlisted_with_schedule_action(self):
        from job_postings.models import Application

        self.client.force_login(self.interviewer)
        resp = self.client.get(reverse("dashboard"))
        short_app = Application.objects.get(candidate__email="s@example.com")
        self.assertContains(resp, "Short Cand")                                   # the schedulable candidate is listed
        self.assertContains(resp, reverse("schedule_interview", args=[short_app.pk]))  # with an actionable schedule link
        self.assertNotContains(resp, "Applied Cand")                             # un-shortlisted ones are not in this list
