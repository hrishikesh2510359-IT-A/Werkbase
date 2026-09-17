from django.urls import path

from . import views

urlpatterns = [
    path('postings/<int:pk>/candidates/', views.ranked_candidates_view, name='ranked_candidates'),
    path('postings/<int:pk>/recompute/', views.recompute_scores_view, name='recompute_scores'),
    path('applications/<int:pk>/', views.score_card_view, name='score_card'),
    path('applications/<int:pk>/<str:action>/', views.update_application_status_view, name='update_application_status'),
]
