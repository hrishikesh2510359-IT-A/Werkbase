"""Interview tests: double-booking rejection, Google Meet fallback, reschedule
regenerates the link, cancel reverts application status. Google is always mocked
- no test touches the real API."""

from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from job_postings.models import Application, JobPosting

from . import services
from .models import Interview

PWD = "Test@1234"


class InterviewServiceTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            email="rec@example.com", password=PWD, full_name="Rec", role=User.Role.HR_RECRUITER
        )
        self.interviewer = User.objects.create_user(
            email="iv@example.com", password=PWD, full_name="Ivy", role=User.Role.INTERVIEWER
        )
        self.candidate = User.objects.create_user(
            email="c@example.com", password=PWD, full_name="Cand", role=User.Role.CANDIDATE
        )
        self.posting = JobPosting.objects.create(
            job_title="Backend", department="ENGINEERING", employment_type="FULL_TIME",
            work_mode="ON_SITE", num_openings=1, location="Chennai",
            status=JobPosting.Status.PUBLISHED, posted_by=self.recruiter,
        )
        self.application = Application.objects.create(
            job_posting=self.posting, candidate=self.candidate,
            resume=SimpleUploadedFile("r.txt", b"cv"), status=Application.Status.SHORTLISTED,
        )
        self.slot = timezone.now() + timedelta(days=1)

    def _schedule(self, at, duration=45, manual_link="https://meet.example/x"):
        return services.schedule_interview(
            application=self.application, interviewer=self.interviewer,
            scheduled_at=at, duration_minutes=duration, manual_link=manual_link,
        )

    def test_google_fallback_flags_manual_link(self):
        # When Google returns (None, None), the manual link is used and flagged.
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            interview = self._schedule(self.slot, manual_link="https://meet.example/manual")
        self.assertTrue(interview.is_manual_link)
        self.assertEqual(interview.meet_link, "https://meet.example/manual")

    def test_google_success_stores_generated_link(self):
        with mock.patch.object(services, "create_meet_event", return_value=("https://meet.google.com/abc", "evt1")):
            interview = self._schedule(self.slot)
        self.assertFalse(interview.is_manual_link)
        self.assertEqual(interview.meet_link, "https://meet.google.com/abc")
        self.assertEqual(interview.calendar_event_id, "evt1")

    def test_schedule_sets_application_interview_scheduled(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            self._schedule(self.slot)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.INTERVIEW_SCHEDULED)

    def test_double_booking_detected(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            self._schedule(self.slot, duration=45)
        # An overlapping slot for the same interviewer must be flagged.
        clash = services.find_conflicting_interview(self.interviewer, self.slot + timedelta(minutes=20), 45)
        self.assertIsNotNone(clash)

    def test_non_overlapping_slot_allowed(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            self._schedule(self.slot, duration=45)
        clear = services.find_conflicting_interview(self.interviewer, self.slot + timedelta(hours=3), 45)
        self.assertIsNone(clear)

    def test_buffer_makes_touching_slots_conflict(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            self._schedule(self.slot, duration=45)
        # 50 min after start = 5 min after the 45-min interview ends, inside the buffer.
        clash = services.find_conflicting_interview(self.interviewer, self.slot + timedelta(minutes=50), 45)
        self.assertIsNotNone(clash)

    def test_reschedule_regenerates_link_and_deletes_old_event(self):
        with mock.patch.object(services, "create_meet_event", return_value=("https://meet.google.com/old", "evt-old")):
            interview = self._schedule(self.slot)
        with mock.patch.object(services, "create_meet_event", return_value=("https://meet.google.com/new", "evt-new")) as mk, \
             mock.patch.object(services, "delete_calendar_event") as rm:
            services.reschedule_interview(interview, scheduled_at=self.slot + timedelta(days=2), duration_minutes=30)
        rm.assert_called_once_with("evt-old")  # old event cleaned up
        mk.assert_called_once()                # a brand-new event, not an edit
        interview.refresh_from_db()
        self.assertEqual(interview.meet_link, "https://meet.google.com/new")
        self.assertEqual(interview.calendar_event_id, "evt-new")

    def test_cancel_reverts_application_to_shortlisted(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            interview = self._schedule(self.slot)
        with mock.patch.object(services, "delete_calendar_event"):
            services.cancel_interview(interview)
        interview.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.CANCELLED)
        self.assertEqual(self.application.status, Application.Status.SHORTLISTED)

    def test_feedback_completes_and_marks_interviewed(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            interview = self._schedule(self.slot)
        services.submit_feedback(interview, rating=4, comment="Good", recommendation=Interview.Recommendation.PROCEED)
        interview.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.COMPLETED)
        self.assertEqual(self.application.status, Application.Status.INTERVIEWED)

    def test_join_available_immediately_not_only_near_the_slot(self):
        # Scheduled a full day away - the Join button must still be available.
        with mock.patch.object(services, "create_meet_event", return_value=("https://meet.google.com/abc", "e1")):
            interview = self._schedule(self.slot)
        self.assertTrue(interview.can_join)

    def test_no_join_without_a_link_or_after_cancel(self):
        with mock.patch.object(services, "create_meet_event", return_value=(None, None)):
            interview = self._schedule(self.slot, manual_link="")
        self.assertFalse(interview.can_join)  # no link at all
        interview.meet_link = "https://meet.example/x"
        interview.status = Interview.Status.CANCELLED
        self.assertFalse(interview.can_join)


class GoogleCredentialModeTests(TestCase):
    """Meet links work with either an OAuth refresh token (plain Gmail) or a
    Workspace service account. Google is never actually called."""

    def test_oauth_settings_enable_google(self):
        from django.test import override_settings
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET="sec",
                               GOOGLE_OAUTH_REFRESH_TOKEN="tok"):
            self.assertTrue(services.google_calendar_enabled())

    def test_partial_oauth_settings_do_not_enable_google(self):
        from django.test import override_settings
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET="",
                               GOOGLE_OAUTH_REFRESH_TOKEN="tok", GOOGLE_CALENDAR_CREDENTIALS_FILE=""):
            self.assertFalse(services.google_calendar_enabled())

    def test_oauth_client_is_built_from_refresh_token(self):
        from django.test import override_settings
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET="sec",
                               GOOGLE_OAUTH_REFRESH_TOKEN="tok"), \
                mock.patch("googleapiclient.discovery.build") as build:
            services._calendar_client()
        creds = build.call_args.kwargs["credentials"]
        self.assertEqual(creds.refresh_token, "tok")
        self.assertEqual(creds.client_id, "id")

    def test_meet_link_is_requested_at_the_scheduled_time(self):
        from django.test import override_settings
        start = timezone.now() + timedelta(days=2)
        fake = mock.MagicMock()
        fake.events.return_value.insert.return_value.execute.return_value = {
            "hangoutLink": "https://meet.google.com/abc-defg-hij", "id": "evt9"}
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET="sec",
                               GOOGLE_OAUTH_REFRESH_TOKEN="tok"), \
                mock.patch.object(services, "_calendar_client", return_value=fake):
            link, event_id = services.create_meet_event(
                summary="s", description="d", start_at=start, duration_minutes=30, attendee_emails=["a@b.c"])
        self.assertEqual((link, event_id), ("https://meet.google.com/abc-defg-hij", "evt9"))
        body = fake.events.return_value.insert.call_args.kwargs["body"]
        self.assertEqual(body["start"]["dateTime"], start.isoformat())
        self.assertIn("conferenceData", body)

