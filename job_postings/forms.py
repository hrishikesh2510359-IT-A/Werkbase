from django import forms

from accounts.models import User

from .models import (
    Application,
    Department,
    EducationLevel,
    EmploymentType,
    JobPosting,
    NOTICE_PERIOD_CHOICES,
    Priority,
    REFERRAL_SOURCE_CHOICES,
    Skill,
    SOFT_SKILL_CHOICES,
    VacancyRequest,
    WorkMode,
)

YES_NO_CHOICES = [("YES", "Yes"), ("NO", "No")]


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
    # Optional: reuses the candidate's profile resume by default (see apply_job_view) -
    # the candidate never has to re-upload unless they want to for this specific job.
    resume = forms.FileField(required=False, help_text="Leave blank to use the resume on your profile.")

    # Short, capped-length and job-specific only - education/experience/skills are
    # already on file and never re-asked here.
    sop_why_role = forms.CharField(
        label="Why this role?", max_length=400, widget=forms.Textarea(attrs={"rows": 3, "maxlength": 400})
    )
    sop_why_company = forms.CharField(
        label="Why this company?", max_length=400, widget=forms.Textarea(attrs={"rows": 3, "maxlength": 400})
    )
    expected_salary = forms.IntegerField(min_value=0, required=False)
    soft_skills = forms.MultipleChoiceField(
        label="Top 3 Soft Skills", choices=SOFT_SKILL_CHOICES, widget=forms.CheckboxSelectMultiple, required=False
    )

    # Standard ATS knockout/metadata questions - structured (Yes/No, dropdown, URL),
    # never open-ended.
    work_authorized = forms.ChoiceField(
        label="Are you legally authorized to work in this country?", choices=YES_NO_CHOICES, widget=forms.RadioSelect
    )
    requires_sponsorship = forms.ChoiceField(
        label="Will you now or in the future require visa sponsorship?", choices=YES_NO_CHOICES, widget=forms.RadioSelect
    )
    notice_period = forms.ChoiceField(label="Notice Period", choices=NOTICE_PERIOD_CHOICES)
    # Only asked at all for non-remote postings - see __init__.
    willing_to_relocate = forms.ChoiceField(
        label="Are you willing to relocate for this role?", choices=YES_NO_CHOICES, widget=forms.RadioSelect, required=False
    )
    linkedin_url = forms.URLField(label="LinkedIn URL", required=False)
    portfolio_url = forms.URLField(label="Portfolio / GitHub URL", required=False)
    referral_source = forms.ChoiceField(label="How did you hear about this opening?", choices=REFERRAL_SOURCE_CHOICES)
    referred_by = forms.CharField(label="Referred by (if any)", max_length=150, required=False)

    def __init__(self, *args, posting=None, **kwargs):
        super().__init__(*args, **kwargs)
        # A remote posting makes "willing to relocate" meaningless - don't ask it.
        if posting is not None and posting.work_mode == WorkMode.REMOTE:
            del self.fields["willing_to_relocate"]

    def clean_soft_skills(self):
        selected = self.cleaned_data["soft_skills"]
        if len(selected) > 3:
            raise forms.ValidationError("Pick at most 3 soft skills.")
        return selected
