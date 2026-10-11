from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from interviews.models import Interview, Notification
from job_postings.models import Application, JobPosting, VacancyRequest

from .decorators import hr_admin_required
from .forms import CompanyUserCreateForm, LoginForm
from .google_login import google_login_enabled
from .models import User


def _dashboard_context(user):
    """Builds real, per-user (never just per-role) data for the dashboard.
    See job_postings.views for the ownership-scoped queries this reuses the same pattern from."""
    if user.role == User.Role.HIRING_MANAGER:
        # Ownership boundary: only this Hiring Manager's own requests.
        my_requests = VacancyRequest.objects.filter(raised_by=user)
        return {
            "recent_requests": my_requests[:5],
            "pending_requests": my_requests.filter(status=VacancyRequest.Status.PENDING),
            "stats": [
                {"label": "Pending", "value": my_requests.filter(status=VacancyRequest.Status.PENDING).count()},
                {"label": "Posted", "value": my_requests.filter(status=VacancyRequest.Status.POSTED).count()},
            ],
        }

    if user.role == User.Role.HR_RECRUITER:
        # Ownership boundary: only this recruiter's own postings.
        my_postings = JobPosting.objects.filter(posted_by=user)
        pending_queue = VacancyRequest.objects.filter(status=VacancyRequest.Status.PENDING)
        # Applicants surface here so the recruiter never has to hunt for them:
        # everyone still waiting for a shortlist decision, best match first.
        my_applications = Application.objects.filter(job_posting__posted_by=user)
        new_applicants = (
            my_applications.filter(status=Application.Status.APPLIED)
            .select_related("candidate", "job_posting")
            .order_by(F("match_score").desc(nulls_last=True), "applied_at")
        )
        recent_postings = my_postings.annotate(
            applicant_count=Count("applications"),
            new_count=Count("applications", filter=Q(applications__status=Application.Status.APPLIED)),
        )[:5]
        return {
            "recent_postings": recent_postings,
            "pending_queue": pending_queue,
            "new_applicants": new_applicants[:8],
            "new_applicants_total": new_applicants.count(),
            "stats": [
                {"label": "New applicants", "value": new_applicants.count()},
                {"label": "Shortlisted", "value": my_applications.filter(status=Application.Status.SHORTLISTED).count()},
                {"label": "Published", "value": my_postings.filter(status=JobPosting.Status.PUBLISHED).count()},
            ],
        }

    if user.role == User.Role.INTERVIEWER:
        postings_with_applicants = (
            JobPosting.objects.filter(applications__status__in=[Application.Status.APPLIED, Application.Status.SHORTLISTED])
            .distinct()
            .prefetch_related("applications__candidate")
        )
        # An interviewer acts only on SHORTLISTED candidates (scheduling is gated
        # to Shortlisted everywhere). Filtering APPLIED here made this list a dead
        # end - it never surfaced an actionable candidate. Shortlisted = "ready to
        # be interviewed".
        pending_applicants = Application.objects.filter(status=Application.Status.SHORTLISTED).select_related("candidate", "job_posting")

        # The interviewer's own upcoming interviews, surfaced on the dashboard so
        # they can join / reschedule / cancel / give feedback the moment they log
        # in - no click-through needed.
        now = timezone.now()
        upcoming = (
            Interview.objects.filter(interviewer=user, status=Interview.Status.SCHEDULED)
            .select_related("application__job_posting", "application__candidate")
            .order_by("scheduled_at")
        )
        interview_rows = [{
            "interview": iv,
            "can_join": iv.can_join,
            "can_give_feedback": now >= iv.scheduled_at,
        } for iv in upcoming]

        return {
            "postings_with_applicants": postings_with_applicants[:5],
            "pending_applicants": pending_applicants,
            "interview_rows": interview_rows,
            "stats": [
                {"label": "Upcoming interviews", "value": upcoming.count()},
                {"label": "Awaiting interview", "value": pending_applicants.count()},
                {"label": "Postings with applicants", "value": postings_with_applicants.count()},
            ],
        }

    if user.role == User.Role.CANDIDATE:
        # Ownership boundary: only this candidate's own applications.
        my_applications = Application.objects.filter(candidate=user).select_related("job_posting")
        # Ownership boundary: only this candidate's own interviews. Surfaced on
        # the dashboard so the Join button is one click away, not inside
        # My Applications.
        upcoming_interviews = (
            Interview.objects.filter(application__candidate=user, status=Interview.Status.SCHEDULED)
            .select_related("application__job_posting", "interviewer")
            .order_by("scheduled_at")
        )
        return {
            "upcoming_interviews": upcoming_interviews,
            "recent_jobs": JobPosting.objects.filter(status=JobPosting.Status.PUBLISHED).exclude(
                applications__candidate=user, applications__status=Application.Status.SELECTED
            )[:5],
            "recent_applications": my_applications[:5],
            "unread_notifications": Notification.objects.filter(recipient=user, is_read=False).count(),
            "stats": [
                {"label": "Applications submitted", "value": my_applications.count()},
                {"label": "Upcoming interviews", "value": upcoming_interviews.count()},
            ],
        }

    if user.role == User.Role.HR_ADMIN:
        return {
            "recent_users": User.objects.exclude(role=User.Role.CANDIDATE).order_by("-date_joined")[:5],
            "stats": [
                {"label": "Company user accounts", "value": User.objects.exclude(role=User.Role.CANDIDATE).count()},
            ],
        }

    if user.role == User.Role.HR_MANAGER:
        # Interviewed candidates awaiting the HR Manager's final decision.
        awaiting = (
            Application.objects.filter(status=Application.Status.INTERVIEWED)
            .select_related("candidate", "job_posting")[:5]
        )
        return {
            "hm_awaiting": awaiting,
            "stats": [
                {"label": "Awaiting decision", "value": Application.objects.filter(status=Application.Status.INTERVIEWED).count()},
                {"label": "Selected", "value": Application.objects.filter(status=Application.Status.SELECTED).count()},
            ],
        }

    # Onboarding Coordinator: no workflow exists for them yet in this
    # module - an honest placeholder, no fake numbers, no dead buttons.
    return {}


def portal_select_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'accounts/portal_select.html')


def candidate_login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = request.session.pop('login_error', None)
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password'],
            )
            # Security boundary: candidate portal only accepts candidate accounts,
            # even if the credentials are otherwise valid for a company account.
            if user is not None and user.role == User.Role.CANDIDATE:
                login(request, user)
                return redirect('dashboard')
            error = "Invalid email or password"
    else:
        form = LoginForm()

    return render(request, 'accounts/candidate_login.html', {'form': form, 'error': error, 'google_enabled': google_login_enabled()})


def company_login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = request.session.pop('login_error', None)
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password'],
            )
            # Security boundary: company portal rejects candidate accounts,
            # even if the credentials are otherwise valid.
            if user is not None and user.role != User.Role.CANDIDATE:
                login(request, user)
                return redirect('dashboard')
            error = "Invalid email or password"
    else:
        form = LoginForm()

    return render(request, 'accounts/company_login.html', {'form': form, 'error': error, 'google_enabled': google_login_enabled()})


@hr_admin_required
def create_company_user_view(request):
    # Security boundary: only an authenticated HR Admin reaches this view
    # (enforced by hr_admin_required) and only company roles are offered/accepted.
    created_user = None
    if request.method == 'POST':
        form = CompanyUserCreateForm(request.POST)
        if form.is_valid():
            # Re-check server-side even though the decorator already gated this view -
            # never trust that the template hid the option for anyone else.
            if request.user.role != User.Role.HR_ADMIN:
                raise PermissionDenied("Only HR Admin can create company users.")
            created_user = User.objects.create_user(
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password1'],
                full_name=form.cleaned_data['full_name'],
                role=form.cleaned_data['role'],
            )
            form = CompanyUserCreateForm()
    else:
        form = CompanyUserCreateForm()

    return render(request, 'accounts/create_user.html', {'form': form, 'created_user': created_user})


@login_required
def dashboard_view(request):
    context = _dashboard_context(request.user)
    context["active_nav"] = "dashboard"
    return render(request, 'accounts/dashboard.html', context)


def logout_view(request):
    logout(request)
    return redirect('portal_select')
