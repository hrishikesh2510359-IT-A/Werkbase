from django.urls import path

from . import views

urlpatterns = [
    path('decisions/', views.hiring_decisions_view, name='hiring_decisions'),
    path('decisions/<int:pk>/<str:action>/', views.decide_view, name='decide_application'),
    path('offer/<int:pk>/', views.offer_letter_view, name='offer_letter'),
    path('offer/<int:pk>/celebrated/', views.celebration_seen_view, name='celebration_seen'),
    path('offer/<int:pk>/resend/', views.resend_offer_email_view, name='resend_offer_email'),
    path('offer/<int:pk>/pdf/', views.offer_letter_pdf_view, name='offer_letter_pdf'),
]
