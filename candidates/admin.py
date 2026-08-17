from django.contrib import admin

from .models import CandidateProfile, EducationEntry, ExperienceEntry

admin.site.register(CandidateProfile)
admin.site.register(EducationEntry)
admin.site.register(ExperienceEntry)
