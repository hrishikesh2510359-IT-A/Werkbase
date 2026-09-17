"""Analytics aggregation tests on a small known dataset, plus empty-state, and
the one full-pipeline integration test (scoped to the implemented flow)."""

from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from interviews import services as interview_services
from interviews.models import Interview
from job_postings.models import Application, JobPosting, Skill

from . import metrics

PWD = "Test@1234"


def make_posting(owner, title="Backend"):
    return JobPosting.objects.create(
        job_title=title, department="ENGINEERING", employment_type="FULL_TIME",
        work_mode="ON_SITE", num_openings=1, location="Chennai",
        status=JobPosting.Status.PUBLISHED, posted_by=owner,
    )


class AnalyticsMetricsTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        self.interviewer = User.objects.create_user(
            email="iv@example.com", password=PWD, full_name="Ivy", role=User.Role.INTERVIEWER
        )
        self.posting = make_posting(self.recruiter)

        # Known dataset: 4 applications at increasing pipeline stages.
        self.a_applied = self._app("a1", Application.Status.APPLIED, "LINKEDIN")
        self.a_short = self._app("a2", Application.Status.SHORTLISTED, "LINKEDIN")
        self.a_sched = self._app("a3", Application.Status.INTERVIEW_SCHEDULED, "EMPLOYEE_REFERRAL")
        self.a_interviewed = self._app("a4", Application.Status.INTERVIEWED, "EMPLOYEE_REFERRAL")

        # The interviewed one has a PROCEED interview -> counts as "selected".
        Interview.objects.create(
            application=self.a_interviewed, interviewer=self.interviewer,
            scheduled_at=timezone.now() - timedelta(days=1), duration_minutes=45,
            status=Interview.Status.COMPLETED, recommendation=Interview.Recommendation.PROCEED,
            feedback_submitted_at=timezone.now(),
        )

    def _app(self, email_prefix, status, source):
        cand = User.objects.create_user(
            email=f"{email_prefix}@example.com", password=PWD, full_name=email_prefix, role=User.Role.CANDIDATE
        )
        return Application.objects.create(
            job_posting=self.posting, candidate=cand,
            resume=SimpleUploadedFile("r.txt", b"cv"), status=status, referral_source=source,
        )

    def test_summary_counts(self):
        c = metrics.summary_counts()
        self.assertEqual(c["total_postings"], 1)
        self.assertEqual(c["total_applications"], 4)
        self.assertEqual(c["total_selected"], 1)   # the PROCEED one
        self.assertEqual(c["total_interviews"], 1)

    def test_funnel_is_cumulative(self):
        f = metrics.application_funnel()
        # applied>=shortlisted>=scheduled>=interviewed>=selected
        self.assertEqual(f["values"], [4, 3, 2, 1, 1])

    def test_source_breakdown(self):
        s = metrics.source_breakdown()
        by_label = dict(zip(s["labels"], s["values"]))
        self.assertEqual(by_label["LinkedIn"], 2)
        self.assertEqual(by_label["Employee Referral"], 2)

    def test_time_to_hire_excludes_jobs_without_hire(self):
        empty_posting = make_posting(self.recruiter, title="No Hires Yet")
        cand = User.objects.create_user(
            email="nohire@example.com", password=PWD, full_name="NH", role=User.Role.CANDIDATE
        )
        Application.objects.create(
            job_posting=empty_posting, candidate=cand,
            resume=SimpleUploadedFile("r.txt", b"cv"), status=Application.Status.APPLIED,
        )
        rows = metrics.time_to_hire()
        titles = [r["job_title"] for r in rows]
        self.assertIn("Backend", titles)
        self.assertNotIn("No Hires Yet", titles)  # excluded, not counted as zero

    def test_empty_database_does_not_crash(self):
        Application.objects.all().delete()
        JobPosting.objects.all().delete()
        self.assertEqual(metrics.summary_counts()["total_applications"], 0)
        self.assertEqual(metrics.application_funnel()["values"], [0, 0, 0, 0, 0])
        self.assertEqual(metrics.time_to_hire(), [])
        self.assertEqual(metrics.source_breakdown()["values"], [])


class FullPipelineIntegrationTest(TestCase):
    """HR Recruiter posts job -> Candidate applies -> scored -> shortlisted ->
    Interviewer schedules -> interview completed with feedback -> reflected in
    Analytics. Stops at Interviewer feedback: HR Manager approval / Onboarding
    aren't built yet (known limitation, not tested)."""

    def test_end_to_end_flow(self):
        recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        interviewer = User.objects.create_user(
            email="iv@example.com", password=PWD, full_name="Ivy", role=User.Role.INTERVIEWER
        )
        candidate = User.objects.create_user(
            email="c@example.com", password=PWD, full_name="Cand", role=User.Role.CANDIDATE
        )

        # 1. Recruiter posts a job with required skills.
        posting = make_posting(recruiter)
        py = Skill.objects.create(name="Python")
        posting.required_skills.add(py)

        # 2. Candidate applies (profile has the matching skill).
        from candidates.models import CandidateProfile
        profile = CandidateProfile.objects.create(user=candidate)
        profile.skills.add(py)
        application = Application.objects.create(
            job_posting=posting, candidate=candidate,
            resume=SimpleUploadedFile("r.txt", b"cv"), status=Application.Status.APPLIED,
        )

        # 3. Resume scored.
        from screening.services import score_application
        score_application(application)
        application.refresh_from_db()
        self.assertEqual(application.match_score, 100.0)

        # 4. Recruiter shortlists (via the real view, exercising the handoff).
        self.client.force_login(recruiter)
        self.client.post(reverse_shortlist(application.pk))
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SHORTLISTED)

        # 5. Interviewer schedules.
        with mock.patch.object(interview_services, "create_meet_event", return_value=(None, None)):
            interview = interview_services.schedule_interview(
                application=application, interviewer=interviewer,
                scheduled_at=timezone.now() + timedelta(days=1), duration_minutes=45,
                manual_link="https://meet.example/x",
            )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.INTERVIEW_SCHEDULED)

        # 6. Interview completed with a PROCEED recommendation.
        interview_services.submit_feedback(
            interview, rating=5, comment="Great", recommendation=Interview.Recommendation.PROCEED
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.INTERVIEWED)

        # 7. Analytics reflects the whole journey.
        funnel = metrics.application_funnel()
        self.assertEqual(funnel["values"], [1, 1, 1, 1, 1])
        self.assertEqual(metrics.summary_counts()["total_selected"], 1)
        tth = metrics.time_to_hire()
        self.assertEqual(len(tth), 1)
        self.assertGreaterEqual(tth[0]["hires"], 1)


def reverse_shortlist(pk):
    from django.urls import reverse
    return reverse("update_application_status", args=[pk, "shortlist"])
