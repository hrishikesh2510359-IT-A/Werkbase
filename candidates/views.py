from django.contrib import messages
from django.contrib.auth import login
from django.core.files.storage import default_storage
from django.shortcuts import redirect, render

from accounts.decorators import role_required
from accounts.models import User
from job_postings.models import Skill

from .forms import (
    CandidateProfileForm,
    CandidateSignupStep1Form,
    EducationFormSet,
    ExperienceFormSet,
    match_master_skills,
)
from .models import CandidateProfile, EducationEntry, ExperienceEntry
from .resume_parser import parse_resume

PENDING_SIGNUP_SESSION_KEY = "pending_candidate_signup"

ENTRY_FIELDS = {
    "education": ["institute_name", "degree", "field_of_study", "start_year", "end_year", "grade"],
    "experience": ["company_name", "job_title", "start_date", "end_date", "description"],
}


def candidate_signup_step1_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = CandidateSignupStep1Form(request.POST, request.FILES)
        if form.is_valid():
            resume_file = form.cleaned_data["resume"]
            extracted = parse_resume(resume_file)

            # Files can't live in the session - stash the upload under a pending
            # path now; step 2 points the CandidateProfile.resume field at it directly.
            resume_file.seek(0)
            pending_path = default_storage.save(f"resumes/pending/{resume_file.name}", resume_file)

            request.session[PENDING_SIGNUP_SESSION_KEY] = {
                "full_name": form.cleaned_data["full_name"],
                "email": form.cleaned_data["email"],
                "password": form.cleaned_data["password1"],
                "resume_path": pending_path,
                "extracted": extracted,
            }
            return redirect("candidate_signup_review")
    else:
        form = CandidateSignupStep1Form()

    return render(request, "candidates/signup_step1.html", {"form": form})


def candidate_signup_review_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    pending = request.session.get(PENDING_SIGNUP_SESSION_KEY)
    if not pending:
        messages.error(request, "Please start signup again.")
        return redirect("candidate_signup")

    extracted = pending["extracted"]
    master_skill_names = list(Skill.objects.values_list("name", flat=True))

    if request.method == "POST":
        profile_form = CandidateProfileForm(request.POST)
        edu_formset = EducationFormSet(request.POST, prefix="edu")
        exp_formset = ExperienceFormSet(request.POST, prefix="exp")

        if profile_form.is_valid() and edu_formset.is_valid() and exp_formset.is_valid():
            # Race-condition safety: the email may have been taken by someone
            # else while this candidate was filling out step 2.
            if User.objects.filter(email=pending["email"]).exists():
                del request.session[PENDING_SIGNUP_SESSION_KEY]
                messages.error(request, "That email was registered while you were completing your profile. Please sign up again.")
                return redirect("candidate_signup")

            # Security boundary: role is hardcoded, never taken from submitted data.
            user = User.objects.create_user(
                email=pending["email"],
                password=pending["password"],
                full_name=pending["full_name"],
                role=User.Role.CANDIDATE,
            )
            profile = CandidateProfile.objects.create(user=user, phone=profile_form.cleaned_data["phone"])

            pending_path = pending.get("resume_path")
            if pending_path and default_storage.exists(pending_path):
                profile.resume.name = pending_path
                profile.save(update_fields=["resume"])

            profile.skills.set(match_master_skills(profile_form.cleaned_data["skills"]))

            for edu_form in edu_formset:
                data = edu_form.cleaned_data
                if data.get("DELETE") or not any(data.get(f) for f in ENTRY_FIELDS["education"]):
                    continue
                EducationEntry.objects.create(profile=profile, **{f: data.get(f, "") for f in ENTRY_FIELDS["education"]})

            for exp_form in exp_formset:
                data = exp_form.cleaned_data
                if data.get("DELETE") or not any(data.get(f) for f in ENTRY_FIELDS["experience"]):
                    continue
                ExperienceEntry.objects.create(profile=profile, **{f: data.get(f, "") for f in ENTRY_FIELDS["experience"]})

            del request.session[PENDING_SIGNUP_SESSION_KEY]
            login(request, user)
            messages.success(request, "Welcome to Werkbase! Your profile is set up.")
            return redirect("dashboard")
    else:
        profile_form = CandidateProfileForm(initial={
            "phone": extracted.get("phone", ""),
            "skills": ",".join(extracted.get("skills", [])),
        })
        edu_formset = EducationFormSet(initial=extracted.get("education", []), prefix="edu")
        exp_formset = ExperienceFormSet(initial=extracted.get("experience", []), prefix="exp")

    return render(request, "candidates/signup_review.html", {
        "profile_form": profile_form,
        "edu_formset": edu_formset,
        "exp_formset": exp_formset,
        "extracted_email": extracted.get("email", ""),
        "master_skill_names": master_skill_names,
    })


@role_required(User.Role.CANDIDATE)
def my_profile_view(request):
    profile, _ = CandidateProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        profile_form = CandidateProfileForm(request.POST)
        edu_formset = EducationFormSet(request.POST, prefix="edu")
        exp_formset = ExperienceFormSet(request.POST, prefix="exp")
        if profile_form.is_valid() and edu_formset.is_valid() and exp_formset.is_valid():
            # Ownership boundary: a candidate only ever edits their own profile,
            # enforced implicitly since `profile` is fetched by request.user above.
            profile.phone = profile_form.cleaned_data["phone"]
            profile.save(update_fields=["phone"])
            profile.skills.set(match_master_skills(profile_form.cleaned_data["skills"]))

            profile.education_entries.all().delete()
            for edu_form in edu_formset:
                data = edu_form.cleaned_data
                if data.get("DELETE") or not any(data.get(f) for f in ENTRY_FIELDS["education"]):
                    continue
                EducationEntry.objects.create(profile=profile, **{f: data.get(f, "") for f in ENTRY_FIELDS["education"]})

            profile.experience_entries.all().delete()
            for exp_form in exp_formset:
                data = exp_form.cleaned_data
                if data.get("DELETE") or not any(data.get(f) for f in ENTRY_FIELDS["experience"]):
                    continue
                ExperienceEntry.objects.create(profile=profile, **{f: data.get(f, "") for f in ENTRY_FIELDS["experience"]})

            messages.success(request, "Profile updated.")
            return redirect("my_profile")
    else:
        profile_form = CandidateProfileForm(initial={
            "phone": profile.phone,
            "skills": ",".join(profile.skills.values_list("name", flat=True)),
        })
        edu_initial = list(profile.education_entries.values(*ENTRY_FIELDS["education"]))
        exp_initial = list(profile.experience_entries.values(*ENTRY_FIELDS["experience"]))
        edu_formset = EducationFormSet(initial=edu_initial, prefix="edu")
        exp_formset = ExperienceFormSet(initial=exp_initial, prefix="exp")

    return render(request, "candidates/my_profile.html", {
        "profile": profile,
        "profile_form": profile_form,
        "edu_formset": edu_formset,
        "exp_formset": exp_formset,
        "master_skill_names": list(Skill.objects.values_list("name", flat=True)),
    })
