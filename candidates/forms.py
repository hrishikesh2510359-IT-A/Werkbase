from django import forms

from accounts.models import User
from job_postings.models import Skill


class CandidateSignupStep1Form(forms.Form):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    resume = forms.FileField(help_text="PDF, DOCX, or TXT. We'll pre-fill your profile from it on the next step.")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm Password", widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_resume(self):
        resume = self.cleaned_data["resume"]
        if not resume.name.lower().endswith((".pdf", ".docx", ".txt")):
            raise forms.ValidationError("Please upload a PDF, DOCX, or TXT resume.")
        return resume

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data


class CandidateProfileForm(forms.Form):
    phone = forms.CharField(max_length=20, required=False)
    # Tag-input hidden field, same pattern as JobPostingForm's skill fields -
    # but matched only against the existing master list, never used to create new skills.
    skills = forms.CharField(required=False, widget=forms.HiddenInput)


class EducationEntryForm(forms.Form):
    institute_name = forms.CharField(max_length=200, required=False)
    degree = forms.CharField(max_length=150, required=False)
    field_of_study = forms.CharField(max_length=150, required=False)
    start_year = forms.CharField(max_length=10, required=False)
    end_year = forms.CharField(max_length=10, required=False)
    grade = forms.CharField(max_length=50, required=False)


class ExperienceEntryForm(forms.Form):
    company_name = forms.CharField(max_length=200, required=False)
    job_title = forms.CharField(max_length=150, required=False)
    start_date = forms.CharField(max_length=50, required=False)
    end_date = forms.CharField(max_length=50, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


EducationFormSet = forms.formset_factory(EducationEntryForm, extra=3, can_delete=True)
ExperienceFormSet = forms.formset_factory(ExperienceEntryForm, extra=3, can_delete=True)


def match_master_skills(raw_value):
    """Matches comma-separated tag text against the existing master Skill list only -
    never creates a new Skill. Unmatched tags are silently dropped (accuracy over cleverness)."""
    names = [n.strip() for n in raw_value.split(",") if n.strip()]
    matched, seen_ids = [], set()
    for name in names:
        skill = Skill.objects.filter(name__iexact=name).first()
        if skill and skill.id not in seen_ids:
            matched.append(skill)
            seen_ids.add(skill.id)
    return matched
