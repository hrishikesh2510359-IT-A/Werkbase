from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .decorators import hr_admin_required
from .forms import CandidateSignupForm, CompanyUserCreateForm, LoginForm
from .models import User

GOOGLE_DEMO_EMAIL = "google.demo@werkbase.com"

ROLE_CARDS = {
    User.Role.HIRING_MANAGER: [
        {"label": "Open Job Postings", "value": 4},
        {"label": "Applications Received", "value": 27},
        {"label": "Interviews Scheduled This Week", "value": 6},
        {"label": "Candidates Shortlisted", "value": 9},
    ],
    User.Role.HR_RECRUITER: [
        {"label": "Open Job Postings", "value": 4},
        {"label": "Applications Received", "value": 27},
        {"label": "Interviews Scheduled This Week", "value": 6},
        {"label": "Candidates Shortlisted", "value": 9},
    ],
    User.Role.HR_MANAGER: [
        {"label": "Open Job Postings", "value": 4},
        {"label": "Applications Received", "value": 27},
        {"label": "Interviews Scheduled This Week", "value": 6},
        {"label": "Candidates Shortlisted", "value": 9},
    ],
    User.Role.INTERVIEWER: [
        {"label": "Interviews Scheduled", "value": 5},
        {"label": "Feedback Pending", "value": 2},
        {"label": "Completed This Month", "value": 11},
    ],
    User.Role.ONBOARDING_COORDINATOR: [
        {"label": "Offers Sent", "value": 8},
        {"label": "Joining Formalities Pending", "value": 3},
    ],
    User.Role.HR_ADMIN: [
        {"label": "Active User Accounts", "value": 7},
        {"label": "System Configuration", "value": "All systems normal"},
    ],
    User.Role.CANDIDATE: [
        {"label": "Applications Submitted", "value": 3},
        {"label": "Application Status", "value": "Under Review"},
        {"label": "Upcoming Interview", "value": "No interviews scheduled"},
    ],
}


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
    cards = ROLE_CARDS.get(request.user.role, [])
    return render(request, 'accounts/dashboard.html', {'cards': cards})


def logout_view(request):
    logout(request)
    return redirect('portal_select')
