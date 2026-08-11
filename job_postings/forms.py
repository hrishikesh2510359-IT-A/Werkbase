from django import forms

from accounts.models import User

from .models import (
    Application,
    Department,
    EducationLevel,
    EmploymentType,
    JobPosting,
    Priority,
    Skill,
    VacancyRequest,
    WorkMode,
)


class VacancyRequestForm(forms.Form):
    job_title = forms.CharField(max_length=150)
    department = forms.ChoiceField(choices=Department.choices)
    num_openings = forms.IntegerField(min_value=1, initial=1)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    priority = forms.ChoiceField(choices=Priority.choices, initial=Priority.MEDIUM)


def _parse_skill_tags(raw_value):
    """Turns a comma-separated tag-input value into de-duplicated Skill instances."""
    names = [n.strip() for n in raw_value.split(",") if n.strip()]
    skills = []
    for name in dict.fromkeys(names):  # de-dupe, preserve order
        skill, _ = Skill.objects.get_or_create(name__iexact=name, defaults={"name": name})
        skills.append(skill)
    return skills


class JobPostingForm(forms.Form):
    # Section 1 — Basics
    job_title = forms.CharField(max_length=150)
    department = forms.ChoiceField(choices=Department.choices)
    employment_type = forms.ChoiceField(choices=EmploymentType.choices)
    work_mode = forms.ChoiceField(choices=WorkMode.choices)
    num_openings = forms.IntegerField(min_value=1, initial=1)
    location = forms.CharField(max_length=150)

    # Section 2 — Requirements. These arrive as comma-separated tag-input
    # strings and are converted to structured Skill relations in save().
    required_skills = forms.CharField(required=False, widget=forms.HiddenInput)
    nice_to_have_skills = forms.CharField(required=False, widget=forms.HiddenInput)
    min_experience_years = forms.IntegerField(min_value=0, initial=0)
    education_level = forms.ChoiceField(choices=EducationLevel.choices, initial=EducationLevel.ANY)
    key_responsibilities = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))

    # Section 3 — Meta
    reporting_to = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.HIRING_MANAGER), required=False
    )
    salary_range = forms.CharField(max_length=100, required=False)
    closing_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    internal_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def save(self, *, posted_by, vacancy_request=None, status, instance=None):
        """Creates a new JobPosting, or updates `instance` in place when editing
        an existing one owned by the caller (ownership is checked by the view)."""
        field_values = dict(
            job_title=self.cleaned_data["job_title"],
            department=self.cleaned_data["department"],
            employment_type=self.cleaned_data["employment_type"],
            work_mode=self.cleaned_data["work_mode"],
            num_openings=self.cleaned_data["num_openings"],
            location=self.cleaned_data["location"],
            min_experience_years=self.cleaned_data["min_experience_years"],
            education_level=self.cleaned_data["education_level"],
            key_responsibilities=self.cleaned_data["key_responsibilities"],
            reporting_to=self.cleaned_data["reporting_to"],
            salary_range=self.cleaned_data["salary_range"],
            closing_date=self.cleaned_data["closing_date"],
            internal_notes=self.cleaned_data["internal_notes"],
            status=status,
        )
        if instance is not None:
            for field, value in field_values.items():
                setattr(instance, field, value)
            instance.save()
            posting = instance
        else:
            posting = JobPosting.objects.create(vacancy_request=vacancy_request, posted_by=posted_by, **field_values)

        posting.required_skills.set(_parse_skill_tags(self.cleaned_data["required_skills"]))
        posting.nice_to_have_skills.set(_parse_skill_tags(self.cleaned_data["nice_to_have_skills"]))
        return posting


class ApplicationForm(forms.Form):
    resume = forms.FileField()
    cover_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
