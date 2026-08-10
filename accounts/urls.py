from django.urls import path

from . import views

urlpatterns = [
    path('', views.portal_select_view, name='portal_select'),

    # Candidate portal — the only public signup path.
    path('candidate/login/', views.candidate_login_view, name='candidate_login'),
    path('candidate/signup/', views.candidate_signup_view, name='candidate_signup'),

    # Company portal — login only, no public signup link.
    path('company/login/', views.company_login_view, name='company_login'),
    path('company/users/new/', views.create_company_user_view, name='create_company_user'),

    path('auth/google/', views.google_login_stub, name='google_login_stub'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
]
