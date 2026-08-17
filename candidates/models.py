from django.conf import settings
from django.db import models

from job_postings.models import Skill


class CandidateProfile(models.Model):
    # Security boundary: one profile per candidate account, created only through
    # the candidate signup flow (never selectable/creatable by other roles).
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="candidate_profile"
    )
    phone = models.CharField(max_length=20, blank=True)

    # The resume "on file" - reused by default on every application so the
    # candidate never has to re-upload unless they choose to for a specific job.
    resume = models.FileField(upload_to="resumes/profile/%Y/%m/", blank=True)

    # Reuses the SAME master Skill list as job postings (job_postings.Skill) -
    # never a separate/duplicated list, so the future Resume Scoring module can
    # match candidate skills against job required_skills directly.
    skills = models.ManyToManyField(Skill, related_name="candidate_profiles", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile: {self.user.full_name}"


class EducationEntry(models.Model):
    profile = models.ForeignKey(CandidateProfile, on_delete=models.CASCADE, related_name="education_entries")
    institute_name = models.CharField(max_length=200, blank=True)
    degree = models.CharField(max_length=150, blank=True)
    field_of_study = models.CharField(max_length=150, blank=True)
    start_year = models.CharField(max_length=10, blank=True)
    end_year = models.CharField(max_length=10, blank=True)  # may be "Present"
    grade = models.CharField(max_length=50, blank=True)  # CGPA / percentage, as written

    class Meta:
        ordering = ["-end_year", "-start_year"]

    def __str__(self):
        return f"{self.degree} - {self.institute_name}".strip(" -")


class ExperienceEntry(models.Model):
    profile = models.ForeignKey(CandidateProfile, on_delete=models.CASCADE, related_name="experience_entries")
    company_name = models.CharField(max_length=200, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    start_date = models.CharField(max_length=50, blank=True)
    end_date = models.CharField(max_length=50, blank=True)  # may be "Present"
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.job_title} - {self.company_name}".strip(" -")
