"""Workflow notifications: every change tells the right people (and never the
person who just did it), the live poll endpoint pops each one exactly once, and
the recruiter dashboard surfaces applicants for one-click shortlisting."""

from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from interviews import services
from interviews.models import Interview, Notification
from job_postings.forms import NOTICE_PERIOD_CHOICES, REFERRAL_SOURCE_CHOICES
from job_postings.models import Application, JobPosting, VacancyRequest

from .models import User

PWD = "Test@1234"


def make(email, role, name=None):
    return User.objects.create_user(email=email, password=PWD, full_name=name or email.split("@")[0], role=role)


def inbox(user):
    return list(Notification.objects.filter(recipient=user).values_list("message", flat=True))


class NotificationWorkflowTests(TestCase):
    def setUp(self):
        self.hm = make("hm@x.com", User.Role.HIRING_MANAGER, "Hema")
        self.rec = make("rec@x.com", User.Role.HR_RECRUITER, "Rec One")
        self.rec2 = make("rec2@x.com", User.Role.HR_RECRUITER, "Rec Two")
        self.iv = make("iv@x.com", User.Role.INTERVIEWER, "Ivy")
        self.iv2 = make("iv2@x.com", User.Role.INTERVIEWER, "Ian")
        self.mgr = make("mgr@x.com", User.Role.HR_MANAGER, "Manny")
        self.cand = make("cand@x.com", User.Role.CANDIDATE, "Cara Candidate")

        self.vacancy = VacancyRequest.objects.create(
            job_title="Backend Engineer", department="ENGINEERING", num_openings=1,
            description="Need one", raised_by=self.hm,
        )
        self.posting = JobPosting.objects.create(
            job_title="Backend Engineer", department="ENGINEERING", employment_type="FULL_TIME",
            work_mode="ON_SITE", num_openings=1, location="Chennai",
            status=JobPosting.Status.PUBLISHED, posted_by=self.rec, vacancy_request=self.vacancy,
        )
        self.app = Application.objects.create(
            job_posting=self.posting, candidate=self.cand,
            resume=SimpleUploadedFile("r.txt", b"cv"), match_score=82, fit_tier="strong",
        )
        Notification.objects.all().delete()

    def login(self, user):
        self.client.logout()
        self.client.login(email=user.email, password=PWD)

    # --- vacancy / posting ---------------------------------------------------
    def test_raising_vacancy_notifies_all_recruiters_but_not_the_raiser(self):
        Notification.objects.all().delete()
        self.login(self.hm)
        self.client.post(reverse("raise_vacancy"), {
            "job_title": "Data Analyst", "department": "ENGINEERING", "num_openings": 1,
            "description": "Need analyst", "priority": "MEDIUM",
        })
        for r in (self.rec, self.rec2):
            self.assertTrue(any("New vacancy request: Data Analyst" in m for m in inbox(r)))
        self.assertEqual(inbox(self.hm), [])

    def test_publishing_a_posting_notifies_the_hiring_manager(self):
        draft = JobPosting.objects.create(
            job_title="Draft Role", department="ENGINEERING", employment_type="FULL_TIME", work_mode="ON_SITE",
            num_openings=1, location="X", status=JobPosting.Status.DRAFT, posted_by=self.rec,
            vacancy_request=self.vacancy,
        )
        self.login(self.rec)
        self.client.post(reverse("transition_job_posting", args=[draft.pk, "publish"]))
        self.assertTrue(any("Draft Role" in m and "published" in m for m in inbox(self.hm)))
        self.assertEqual(inbox(self.rec), [])

    # --- applying ------------------------------------------------------------
    def test_applying_notifies_candidate_recruiter_and_hiring_manager(self):
        other = make("c2@x.com", User.Role.CANDIDATE, "Dan Candidate")
        self.login(other)
        data = {
            "sop_why_role": "r", "sop_why_company": "c", "work_authorized": "YES", "requires_sponsorship": "NO",
            "notice_period": next(v for v, _ in NOTICE_PERIOD_CHOICES if v),
            "referral_source": next(v for v, _ in REFERRAL_SOURCE_CHOICES if v),
            "resume": SimpleUploadedFile("r.txt", b"python django"), "willing_to_relocate": "YES",
        }
        response = self.client.post(reverse("apply_job", args=[self.posting.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("Application received" in m for m in inbox(other)))
        self.assertTrue(any("New applicant: Dan Candidate" in m for m in inbox(self.rec)))
        self.assertTrue(any("New applicant: Dan Candidate" in m for m in inbox(self.hm)))
        self.assertEqual(inbox(self.rec2), [])  # a different recruiter's job: not their business

    # --- shortlist / reject --------------------------------------------------
    def test_shortlisting_notifies_candidate_and_interviewers(self):
        self.login(self.rec)
        self.client.post(reverse("update_application_status", args=[self.app.pk, "shortlist"]))
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.SHORTLISTED)
        self.assertTrue(any("shortlisted" in m for m in inbox(self.cand)))
        for i in (self.iv, self.iv2):
            self.assertTrue(any("ready to schedule" in m for m in inbox(i)))
        self.assertEqual(inbox(self.rec), [])  # the actor isn't pinged about their own click

    def test_rejecting_notifies_candidate_only(self):
        self.login(self.rec)
        self.client.post(reverse("update_application_status", args=[self.app.pk, "reject"]))
        self.assertTrue(any("won't be moving forward" in m for m in inbox(self.cand)))
        self.assertEqual(inbox(self.iv), [])

    def test_dashboard_shortlist_returns_to_dashboard_and_unsafe_next_is_ignored(self):
        self.login(self.rec)
        url = reverse("update_application_status", args=[self.app.pk, "shortlist"])
        response = self.client.post(url, {"next": reverse("dashboard")})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.app.status = Application.Status.APPLIED
        self.app.save()
        response = self.client.post(url, {"next": "https://evil.example/phish"})
        self.assertRedirects(response, reverse("ranked_candidates", args=[self.posting.pk]), fetch_redirect_response=False)

    def test_other_recruiter_cannot_shortlist(self):
        self.login(self.rec2)
        response = self.client.post(reverse("update_application_status", args=[self.app.pk, "shortlist"]))
        self.assertEqual(response.status_code, 403)

    # --- interview + decision -----------------------------------------------
    def _scheduled(self):
        self.app.status = Application.Status.SHORTLISTED
        self.app.save()
        with mock.patch.object(services, "create_meet_event", return_value=("https://meet.google.com/a", "e")):
            interview = services.schedule_interview(
                application=self.app, interviewer=self.iv,
                scheduled_at=timezone.now() + timedelta(days=1), duration_minutes=30)
        return interview

    def test_scheduling_notifies_candidate_recruiter_and_hiring_manager(self):
        self._scheduled()
        self.assertTrue(any("Interview scheduled for" in m for m in inbox(self.cand)))
        self.assertTrue(any("Interview scheduled: Cara" in m for m in inbox(self.rec)))
        self.assertTrue(any("Interview scheduled: Cara" in m for m in inbox(self.hm)))
        self.assertEqual(inbox(self.iv), [])  # the interviewer scheduled it themself

    def test_reschedule_and_cancel_notify_staff(self):
        interview = self._scheduled()
        Notification.objects.all().delete()
        with mock.patch.object(services, "create_meet_event", return_value=("https://meet.google.com/b", "e2")):
            services.reschedule_interview(interview, scheduled_at=timezone.now() + timedelta(days=2), duration_minutes=30)
        self.assertTrue(any("rescheduled" in m for m in inbox(self.rec)))
        services.cancel_interview(interview)
        self.assertTrue(any("cancelled" in m for m in inbox(self.rec)))
        self.assertTrue(any("cancelled" in m for m in inbox(self.hm)))

    def test_feedback_notifies_hr_managers_and_recruiter(self):
        interview = self._scheduled()
        Notification.objects.all().delete()
        services.submit_feedback(interview, rating=4, comment="solid", recommendation="PROCEED")
        self.assertTrue(any("Ready for a hiring decision" in m for m in inbox(self.mgr)))
        self.assertTrue(any("feedback submitted" in m.lower() for m in inbox(self.rec)))
        self.assertEqual(inbox(self.iv), [])

    def test_final_decision_notifies_everyone_involved(self):
        interview = self._scheduled()
        services.submit_feedback(interview, rating=5, comment="great", recommendation="PROCEED")
        Notification.objects.all().delete()
        self.login(self.mgr)
        joining = (timezone.localdate() + timedelta(days=20)).isoformat()
        self.client.post(reverse("decide_application", args=[self.app.pk, "select"]),
                         {"offer_ctc": "8L", "joining_date": joining})
        self.assertTrue(any("offer letter" in m.lower() for m in inbox(self.cand)))
        for staff in (self.rec, self.hm, self.iv):
            self.assertTrue(any("Hiring decision" in m and "selected" in m for m in inbox(staff)), staff.email)
        self.assertEqual(inbox(self.mgr), [])

    def test_notify_users_dedupes_and_skips_actor(self):
        services.notify_users([self.rec, self.rec, self.hm, None], "hi", actor=self.hm)
        self.assertEqual(Notification.objects.filter(recipient=self.rec).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.hm).count(), 0)


class NotificationDeliveryTests(TestCase):
    def setUp(self):
        self.a = make("a@x.com", User.Role.HR_RECRUITER)
        self.b = make("b@x.com", User.Role.INTERVIEWER)

    def test_poll_returns_each_notification_once_and_only_to_its_owner(self):
        services.notify(self.a, "for A", link="/x/")
        services.notify(self.b, "for B")
        self.client.login(email="a@x.com", password=PWD)
        first = self.client.get(reverse("notifications_poll")).json()
        self.assertEqual([i["message"] for i in first["items"]], ["for A"])
        self.assertEqual(first["unread"], 1)
        second = self.client.get(reverse("notifications_poll")).json()
        self.assertEqual(second["items"], [])          # already popped
        self.assertEqual(second["unread"], 1)           # still unread in the bell

    def test_poll_requires_login(self):
        self.assertEqual(self.client.get(reverse("notifications_poll")).status_code, 302)

    def test_every_role_can_open_notifications_page_and_it_marks_read(self):
        services.notify(self.b, "hello interviewer")
        self.client.login(email="b@x.com", password=PWD)
        response = self.client.get(reverse("my_notifications"))
        self.assertContains(response, "hello interviewer")
        self.assertEqual(Notification.objects.filter(recipient=self.b, is_read=False).count(), 0)

    def test_bell_and_poller_render_for_staff(self):
        self.client.login(email="a@x.com", password=PWD)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'id="bell-dot"')
        self.assertContains(page, reverse("notifications_poll"))


class RecruiterApplicantsUiTests(TestCase):
    def setUp(self):
        self.rec = make("rec@x.com", User.Role.HR_RECRUITER, "Rec")
        self.rec2 = make("rec2@x.com", User.Role.HR_RECRUITER, "Rec2")
        self.cand = make("cand@x.com", User.Role.CANDIDATE, "Zed Zimmer")
        self.cand2 = make("cand2@x.com", User.Role.CANDIDATE, "Yan Hidden")

        def posting(owner, title):
            return JobPosting.objects.create(
                job_title=title, department="ENGINEERING", employment_type="FULL_TIME", work_mode="ON_SITE",
                num_openings=1, location="X", status=JobPosting.Status.PUBLISHED, posted_by=owner)

        self.mine = posting(self.rec, "Mine Job")
        self.theirs = posting(self.rec2, "Their Job")
        self.app = Application.objects.create(job_posting=self.mine, candidate=self.cand,
                                              resume=SimpleUploadedFile("r.txt", b"x"), match_score=90)
        Application.objects.create(job_posting=self.theirs, candidate=self.cand2,
                                   resume=SimpleUploadedFile("r.txt", b"x"), match_score=70)
        self.client.login(email="rec@x.com", password=PWD)

    def test_dashboard_lists_my_new_applicants_with_one_click_actions(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Zed Zimmer")
        self.assertContains(page, reverse("update_application_status", args=[self.app.pk, "shortlist"]))
        self.assertNotContains(page, "Yan Hidden")  # other recruiter's applicant

    def test_dashboard_drops_applicant_once_shortlisted(self):
        self.client.post(reverse("update_application_status", args=[self.app.pk, "shortlist"]),
                         {"next": reverse("dashboard")})
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "No applicants waiting")
        self.assertNotContains(page, reverse("update_application_status", args=[self.app.pk, "shortlist"]))

    def test_job_list_shows_applicant_counts_per_posting(self):
        page = self.client.get(reverse("job_posting_list"))
        self.assertContains(page, "Applicants (1)")
        self.assertContains(page, "1 new")

    def test_ranked_page_shows_names_by_default_and_blind_is_opt_in(self):
        url = reverse("ranked_candidates", args=[self.mine.pk])
        self.assertContains(self.client.get(url), "Zed Zimmer")
        blind = self.client.get(url, {"blind": "1"})
        self.assertNotContains(blind, "Zed Zimmer")
        self.assertContains(blind, f"Candidate #{self.app.pk}")

    def test_ranked_page_status_filter(self):
        url = reverse("ranked_candidates", args=[self.mine.pk])
        self.assertContains(self.client.get(url, {"status": "new"}), "Zed Zimmer")
        self.assertNotContains(self.client.get(url, {"status": "shortlisted"}), "Zed Zimmer")


class CandidateDashboardJoinTests(TestCase):
    """The candidate's Join Google Meet button lives on the dashboard itself."""

    def setUp(self):
        self.rec = make("rec@x.com", User.Role.HR_RECRUITER)
        self.iv = make("iv@x.com", User.Role.INTERVIEWER, "Ivy Interviewer")
        self.me = make("me@x.com", User.Role.CANDIDATE, "Me")
        self.other = make("other@x.com", User.Role.CANDIDATE, "Other")
        self.posting = JobPosting.objects.create(
            job_title="Backend Engineer", department="ENGINEERING", employment_type="FULL_TIME",
            work_mode="ON_SITE", num_openings=1, location="X", status=JobPosting.Status.PUBLISHED, posted_by=self.rec)

    def _interview(self, candidate, link="https://meet.google.com/abc-defg-hij", **extra):
        app = Application.objects.create(job_posting=self.posting, candidate=candidate,
                                         resume=SimpleUploadedFile("r.txt", b"x"),
                                         status=Application.Status.INTERVIEW_SCHEDULED)
        return Interview.objects.create(application=app, interviewer=self.iv, meet_link=link,
                                        scheduled_at=timezone.now() + timedelta(days=3), **extra)

    def test_dashboard_shows_join_button_even_days_ahead(self):
        self._interview(self.me)
        self.client.login(email="me@x.com", password=PWD)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Your Upcoming Interviews")
        self.assertContains(page, 'href="https://meet.google.com/abc-defg-hij"')
        self.assertContains(page, "Join Google Meet")
        self.assertContains(page, "Ivy Interviewer")

    def test_other_candidates_interview_is_never_shown(self):
        self._interview(self.other, link="https://meet.google.com/zzz-zzzz-zzz")
        self.client.login(email="me@x.com", password=PWD)
        page = self.client.get(reverse("dashboard"))
        self.assertNotContains(page, "zzz-zzzz-zzz")
        self.assertNotContains(page, "Your Upcoming Interviews")

    def test_no_link_shows_waiting_message_not_a_broken_button(self):
        self._interview(self.me, link="")
        self.client.login(email="me@x.com", password=PWD)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Meeting link will appear here")
        self.assertNotContains(page, "Join Google Meet")

    def test_cancelled_interview_is_hidden(self):
        self._interview(self.me, status=Interview.Status.CANCELLED)
        self.client.login(email="me@x.com", password=PWD)
        self.assertNotContains(self.client.get(reverse("dashboard")), "Your Upcoming Interviews")
