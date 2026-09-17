from django.urls import path

from . import views

urlpatterns = [
    path('applications/<int:pk>/schedule/', views.schedule_interview_view, name='schedule_interview'),
    path('mine/', views.my_interviews_view, name='my_interviews'),
    path('<int:pk>/reschedule/', views.reschedule_interview_view, name='reschedule_interview'),
    path('<int:pk>/cancel/', views.cancel_interview_view, name='cancel_interview'),
    path('<int:pk>/feedback/', views.submit_feedback_view, name='submit_feedback'),
    path('notifications/', views.my_notifications_view, name='my_notifications'),
]
