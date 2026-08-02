from django.urls import path

from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),
    path('auth/google/', views.google_login_stub, name='google_login_stub'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
]
