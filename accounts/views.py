from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from job_postings.models import Application, JobPosting, VacancyRequest

from .decorators import hr_admin_required
from .forms import CandidateSignupForm, CompanyUserCreateForm, LoginForm
from .models import User

GOOGLE_DEMO_EMAIL = "google.demo@werkbase.com"


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
        return {
            "recent_postings": my_postings[:5],
            "pending_queue": pending_queue,
            "stats": [
                {"label": "Draft", "value": my_postings.filter(status=JobPosting.Status.DRAFT).count()},
                {"label": "Published", "value": my_postings.filter(status=JobPosting.Status.PUBLISHED).count()},
            ],
        }

    if user.role == User.Role.INTERVIEWER:
        postings_with_applicants = (
            JobPosting.objects.filter(applications__status__in=[Application.Status.APPLIED, Application.Status.SHORTLISTED])
            .distinct()
            .prefetch_related("applications__candidate")
        )
        pending_applicants = Application.objects.filter(status=Application.Status.APPLIED).select_related("candidate", "job_posting")
        return {
            "postings_with_applicants": postings_with_applicants[:5],
            "pending_applicants": pending_applicants,
            "stats": [
                {"label": "Postings with applicants", "value": postings_with_applicants.count()},
                {"label": "Awaiting review", "value": pending_applicants.count()},
            ],
        }

    if user.role == User.Role.CANDIDATE:
        # Ownership boundary: only this candidate's own applications.
        my_applications = Application.objects.filter(candidate=user).select_related("job_posting")
        return {
            "recent_jobs": JobPosting.objects.filter(status=JobPosting.Status.PUBLISHED)[:5],
            "recent_applications": my_applications[:5],
            "stats": [
                {"label": "Applications submitted", "value": my_applications.count()},
            ],
        }

    if user.role == User.Role.HR_ADMIN:
        return {
            "recent_users": User.objects.exclude(role=User.Role.CANDIDATE).order_by("-date_joined")[:5],
            "stats": [
                {"label": "Company user accounts", "value": User.objects.exclude(role=User.Role.CANDIDATE).count()},
            ],
        }

    # HR Manager, Onboarding Coordinator: no workflow exists for them yet in this
    # module - an honest placeholder, no fake numbers, no dead buttons.
    return {}


def portal_select_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'accounts/portal_select.html')


def candidate_login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
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

    return render(request, 'accounts/candidate_login.html', {'form': form, 'error': error})


def candidate_signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = CandidateSignupForm(request.POST)
        if form.is_valid():
            # Security boundary: role is never read from the submitted form.
            # Public signup can only ever create a CANDIDATE account.
            user = User.objects.create_user(
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password1'],
                full_name=form.cleaned_data['full_name'],
                role=User.Role.CANDIDATE,
            )
            login(request, user)
            return redirect('dashboard')
    else:
        form = CandidateSignupForm()

    return render(request, 'accounts/candidate_signup.html', {'form': form})


def company_login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
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

    return render(request, 'accounts/company_login.html', {'form': form, 'error': error})


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


def google_login_stub(request):
    # Sprint X: replace with real Google OAuth via django-allauth.
    user, _ = User.objects.get_or_create(
        email=GOOGLE_DEMO_EMAIL,
        defaults={'full_name': 'Google Demo User', 'role': User.Role.HIRING_MANAGER},
    )
    login(request, user)
    return redirect('dashboard')


@login_required
def dashboard_view(request):
    context = _dashboard_context(request.user)
    return render(request, 'accounts/dashboard.html', context)


def logout_view(request):
    logout(request)
    return redirect('portal_select')
