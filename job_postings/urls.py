from django.urls import path

from . import views

urlpatterns = [
    path('vacancy-requests/new/', views.raise_vacancy_view, name='raise_vacancy'),
    path('vacancy-requests/mine/', views.my_vacancy_requests_view, name='my_vacancy_requests'),
    path('vacancy-requests/pending/', views.pending_vacancies_view, name='pending_vacancies'),
    path('vacancy-requests/<int:pk>/edit/', views.edit_vacancy_request_view, name='edit_vacancy_request'),
    path('vacancy-requests/<int:pk>/delete/', views.delete_vacancy_request_view, name='delete_vacancy_request'),

    path('postings/new/', views.create_job_posting_view, name='create_job_posting'),
    path('postings/', views.job_posting_list_view, name='job_posting_list'),
    path('postings/<int:pk>/edit/', views.edit_job_posting_view, name='edit_job_posting'),
    path('postings/<int:pk>/duplicate/', views.duplicate_job_posting_view, name='duplicate_job_posting'),
    path('postings/<int:pk>/delete/', views.delete_job_posting_view, name='delete_job_posting'),
    path('postings/<int:pk>/<str:action>/', views.transition_job_posting_view, name='transition_job_posting'),

    path('browse/', views.browse_jobs_view, name='browse_jobs'),
    path('browse/<int:pk>/', views.job_detail_view, name='job_detail'),
    path('browse/<int:pk>/apply/', views.apply_job_view, name='apply_job'),
    path('applications/mine/', views.my_applications_view, name='my_applications'),

    path('interviewer/', views.interviewer_dashboard_view, name='interviewer_dashboard'),
    path('interviewer/applications/<int:pk>/schedule/', views.schedule_interview_stub_view, name='schedule_interview'),
]
