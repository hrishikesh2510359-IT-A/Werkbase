from django.urls import path

from . import views

urlpatterns = [
    path('signup/', views.candidate_signup_step1_view, name='candidate_signup'),
    path('signup/review/', views.candidate_signup_review_view, name='candidate_signup_review'),
    path('profile/', views.my_profile_view, name='my_profile'),
]
