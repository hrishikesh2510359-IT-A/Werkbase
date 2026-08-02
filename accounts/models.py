from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("full_name", email)
        extra_fields.setdefault("role", "HR_ADMIN")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        HIRING_MANAGER = "HIRING_MANAGER", "Hiring Manager"
        HR_RECRUITER = "HR_RECRUITER", "HR Recruiter"
        INTERVIEWER = "INTERVIEWER", "Interviewer"
        HR_MANAGER = "HR_MANAGER", "HR Manager"
        ONBOARDING_COORDINATOR = "ONBOARDING_COORDINATOR", "Onboarding Coordinator"
        HR_ADMIN = "HR_ADMIN", "HR Admin"
        CANDIDATE = "CANDIDATE", "Candidate"

    username = None
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=32, choices=Role.choices)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.full_name} ({self.email})"
