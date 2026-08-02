from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import LoginForm, SignupForm
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


def login_view(request):
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
            if user is not None:
                login(request, user)
                return redirect('dashboard')
            error = "Invalid email or password"
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {'form': form, 'error': error})


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password1'],
                full_name=form.cleaned_data['full_name'],
                role=form.cleaned_data['role'],
            )
            login(request, user)
            return redirect('dashboard')
    else:
        form = SignupForm()

    return render(request, 'accounts/signup.html', {'form': form})


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
    return redirect('login')
