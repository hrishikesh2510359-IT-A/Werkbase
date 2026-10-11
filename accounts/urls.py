from django.urls import path

from . import google_login, views

urlpatterns = [
    path('', views.portal_select_view, name='portal_select'),

    # Candidate portal — login only; signup now lives in the candidates app
    # (candidates.urls, mounted at 'candidate/') since it's a full profile flow.
    path('candidate/login/', views.candidate_login_view, name='candidate_login'),

    # Company portal — login only, no public signup link.
    path('company/login/', views.company_login_view, name='company_login'),
    path('company/users/new/', views.create_company_user_view, name='create_company_user'),

    path('auth/google/callback/', google_login.google_login_callback, name='google_callback'),
    path('auth/google/<str:portal>/', google_login.google_login_start, name='google_login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
]
