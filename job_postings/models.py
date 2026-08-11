from django.conf import settings
from django.db import models


class Department(models.TextChoices):
    ENGINEERING = "ENGINEERING", "Engineering"
    SALES = "SALES", "Sales"
    MARKETING = "MARKETING", "Marketing"
    HUMAN_RESOURCES = "HUMAN_RESOURCES", "Human Resources"
    FINANCE = "FINANCE", "Finance"
    OPERATIONS = "OPERATIONS", "Operations"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT", "Customer Support"
    OTHER = "OTHER", "Other"


class Priority(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"


class EmploymentType(models.TextChoices):
    FULL_TIME = "FULL_TIME", "Full-time"
    PART_TIME = "PART_TIME", "Part-time"
    CONTRACT = "CONTRACT", "Contract"
    INTERN = "INTERN", "Intern"


class WorkMode(models.TextChoices):
    ON_SITE = "ON_SITE", "On-site"
    HYBRID = "HYBRID", "Hybrid"
    REMOTE = "REMOTE", "Remote"


class EducationLevel(models.TextChoices):
    ANY = "ANY", "Any"
    HIGH_SCHOOL = "HIGH_SCHOOL", "High School"
    BACHELORS = "BACHELORS", "Bachelor's Degree"
    MASTERS = "MASTERS", "Master's Degree"
    PHD = "PHD", "PhD"


class Skill(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class VacancyRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        POSTED = "POSTED", "Posted"

    job_title = models.CharField(max_length=150)
    department = models.CharField(max_length=32, choices=Department.choices)
    num_openings = models.PositiveIntegerField(default=1)
    description = models.TextField(help_text="Need / justification for this hire")
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    # Security boundary: always set server-side from request.user in the view,
    # never taken from submitted form data.
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vacancy_requests"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_title} ({self.get_status_display()})"


class JobPosting(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        CLOSED = "CLOSED", "Closed"

    vacancy_request = models.ForeignKey(
        VacancyRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="job_postings"
    )

    # Section 1 — Basics
    job_title = models.CharField(max_length=150)
    department = models.CharField(max_length=32, choices=Department.choices)
    employment_type = models.CharField(max_length=16, choices=EmploymentType.choices)
    work_mode = models.CharField(max_length=16, choices=WorkMode.choices)
    num_openings = models.PositiveIntegerField(default=1)
    location = models.CharField(max_length=150)

    # Section 2 — Requirements. Skills are a structured, queryable relation
    # (not free text) so the future Resume Screening module can match against them directly.
    required_skills = models.ManyToManyField(Skill, related_name="required_in", blank=True)
    nice_to_have_skills = models.ManyToManyField(Skill, related_name="nice_to_have_in", blank=True)
    min_experience_years = models.PositiveIntegerField(default=0)
    education_level = models.CharField(max_length=16, choices=EducationLevel.choices, default=EducationLevel.ANY)
    key_responsibilities = models.TextField(blank=True)

    # Section 3 — Meta
    reporting_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs_reporting_to",
    )
    salary_range = models.CharField(max_length=100, blank=True)
    closing_date = models.DateField(null=True, blank=True)
    internal_notes = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    # Security boundary: always set server-side from request.user in the view,
    # never taken from submitted form data.
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_postings"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_title} ({self.get_status_display()})"


class Application(models.Model):
    class Status(models.TextChoices):
        APPLIED = "APPLIED", "Applied"
        SHORTLISTED = "SHORTLISTED", "Shortlisted"
        REJECTED = "REJECTED", "Rejected"

    job_posting = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name="applications")

    # Security boundary: always set server-side from request.user in the view,
    # never taken from submitted form data.
    candidate = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications"
    )

    resume = models.FileField(upload_to="resumes/%Y/%m/")
    cover_note = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPLIED)
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-applied_at"]
        constraints = [
            # Data consistency: a candidate may not apply twice to the same posting.
            models.UniqueConstraint(fields=["job_posting", "candidate"], name="one_application_per_candidate_per_job")
        ]

    def __str__(self):
        return f"{self.candidate.full_name} -> {self.job_posting.job_title} ({self.get_status_display()})"
