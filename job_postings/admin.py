from django.contrib import admin

from .models import JobPosting, Skill, VacancyRequest

admin.site.register(Skill)
admin.site.register(VacancyRequest)
admin.site.register(JobPosting)
