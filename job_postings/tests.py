"""Job posting + application tests: lifecycle transitions, per-role permissions,
duplicate-application prevention."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Application, JobPosting, Skill

PWD = "Test@1234"


def make_posting(owner, status=JobPosting.Status.DRAFT, title="Backend Engineer"):
    return JobPosting.objects.create(
        job_title=title,
        department="ENGINEERING",
        employment_type="FULL_TIME",
        work_mode="ON_SITE",
        num_openings=1,
        location="Chennai",
        status=status,
        posted_by=owner,
    )


class JobPostingLifecycleTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        self.other_recruiter = User.objects.create_user(
            email="rec2@example.com", password=PWD, full_name="Rec2", role=User.Role.HR_RECRUITER
        )

    def test_publish_then_close_transition(self):
        posting = make_posting(self.recruiter, status=JobPosting.Status.DRAFT)
        self.client.force_login(self.recruiter)

        self.client.post(reverse("transition_job_posting", args=[posting.pk, "publish"]))
        posting.refresh_from_db()
        self.assertEqual(posting.status, JobPosting.Status.PUBLISHED)

        self.client.post(reverse("transition_job_posting", args=[posting.pk, "close"]))
        posting.refresh_from_db()
        self.assertEqual(posting.status, JobPosting.Status.CLOSED)

    def test_cannot_publish_an_already_published_posting(self):
        posting = make_posting(self.recruiter, status=JobPosting.Status.PUBLISHED)
        self.client.force_login(self.recruiter)
        resp = self.client.post(reverse("transition_job_posting", args=[posting.pk, "publish"]))
        self.assertEqual(resp.status_code, 403)  # invalid transition

    def test_recruiter_cannot_edit_other_recruiters_posting(self):
        posting = make_posting(self.other_recruiter)
        self.client.force_login(self.recruiter)
        self.assertEqual(
            self.client.post(reverse("delete_job_posting", args=[posting.pk])).status_code, 403
        )

    def test_candidate_cannot_reach_posting_management(self):
        candidate = User.objects.create_user(
            email="c@example.com", password=PWD, full_name="C", role=User.Role.CANDIDATE
        )
        self.client.force_login(candidate)
        self.assertEqual(self.client.get(reverse("job_posting_list")).status_code, 403)


class DuplicateApplicationTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        self.candidate = User.objects.create_user(
            email="c@example.com", password=PWD, full_name="C", role=User.Role.CANDIDATE
        )
        self.posting = make_posting(self.recruiter, status=JobPosting.Status.PUBLISHED)

    def test_db_rejects_second_application(self):
        Application.objects.create(
            job_posting=self.posting, candidate=self.candidate,
            resume=SimpleUploadedFile("r.txt", b"resume"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Application.objects.create(
                    job_posting=self.posting, candidate=self.candidate,
                    resume=SimpleUploadedFile("r2.txt", b"resume2"),
                )

    def test_different_candidates_can_apply_to_same_job(self):
        other = User.objects.create_user(
            email="c2@example.com", password=PWD, full_name="C2", role=User.Role.CANDIDATE
        )
        Application.objects.create(
            job_posting=self.posting, candidate=self.candidate,
            resume=SimpleUploadedFile("r.txt", b"x"),
        )
        Application.objects.create(
            job_posting=self.posting, candidate=other,
            resume=SimpleUploadedFile("r.txt", b"x"),
        )
        self.assertEqual(self.posting.applications.count(), 2)
