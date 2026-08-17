from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, models
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.models import User

from .forms import ApplicationForm, JobPostingForm, VacancyRequestForm
from .models import Application, Department, JobPosting, VacancyRequest


# ---------------------------------------------------------------------------
# Hiring Manager — Vacancy Requests
# ---------------------------------------------------------------------------

# Security boundary: only a Hiring Manager may raise a vacancy request.
@role_required(User.Role.HIRING_MANAGER)
def raise_vacancy_view(request):
    if request.method == "POST":
        form = VacancyRequestForm(request.POST)
        if form.is_valid():
            VacancyRequest.objects.create(
                job_title=form.cleaned_data["job_title"],
                department=form.cleaned_data["department"],
                num_openings=form.cleaned_data["num_openings"],
                description=form.cleaned_data["description"],
                priority=form.cleaned_data["priority"],
                raised_by=request.user,  # never taken from the form
            )
            messages.success(request, "Vacancy request submitted. HR Recruiter has been notified.")
            return redirect("my_vacancy_requests")
    else:
        form = VacancyRequestForm()

    return render(request, "job_postings/vacancy_request_form.html", {"form": form})


@role_required(User.Role.HIRING_MANAGER)
def my_vacancy_requests_view(request):
    # Ownership boundary: a Hiring Manager only ever sees requests they personally raised,
    # not every request raised by anyone holding the Hiring Manager role.
    requests = VacancyRequest.objects.filter(raised_by=request.user)
    return render(request, "job_postings/my_vacancy_requests.html", {"requests": requests})


@role_required(User.Role.HIRING_MANAGER)
def edit_vacancy_request_view(request, pk):
    vacancy_request = get_object_or_404(VacancyRequest, pk=pk)
    # Ownership boundary: only the Hiring Manager who raised this specific request may edit it.
    if vacancy_request.raised_by != request.user:
        raise PermissionDenied("You can only edit your own vacancy requests.")
    # Once a recruiter has acted on it, the request is historical - edit the resulting
    # job posting instead of quietly changing what the recruiter already saw.
    if vacancy_request.status != VacancyRequest.Status.PENDING:
        raise PermissionDenied("This request has already been posted and can no longer be edited.")

    if request.method == "POST":
        form = VacancyRequestForm(request.POST)
        if form.is_valid():
            vacancy_request.job_title = form.cleaned_data["job_title"]
            vacancy_request.department = form.cleaned_data["department"]
            vacancy_request.num_openings = form.cleaned_data["num_openings"]
            vacancy_request.description = form.cleaned_data["description"]
            vacancy_request.priority = form.cleaned_data["priority"]
            vacancy_request.save()
            messages.success(request, "Vacancy request updated.")
            return redirect("my_vacancy_requests")
    else:
        form = VacancyRequestForm(initial={
            "job_title": vacancy_request.job_title,
            "department": vacancy_request.department,
            "num_openings": vacancy_request.num_openings,
            "description": vacancy_request.description,
            "priority": vacancy_request.priority,
        })

    return render(request, "job_postings/vacancy_request_form.html", {"form": form, "editing": vacancy_request})


@role_required(User.Role.HIRING_MANAGER)
def delete_vacancy_request_view(request, pk):
    vacancy_request = get_object_or_404(VacancyRequest, pk=pk)
    # Ownership boundary: only the Hiring Manager who raised this specific request may delete it.
    if vacancy_request.raised_by != request.user:
        raise PermissionDenied("You can only delete your own vacancy requests.")
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    if vacancy_request.status != VacancyRequest.Status.PENDING:
        raise PermissionDenied("This request has already been posted and can no longer be deleted.")

    vacancy_request.delete()
    messages.success(request, "Vacancy request deleted.")
    return redirect("my_vacancy_requests")


# ---------------------------------------------------------------------------
# HR Recruiter — Job Postings
# ---------------------------------------------------------------------------

# Security boundary: only an HR Recruiter sees the pending-vacancy queue.
@role_required(User.Role.HR_RECRUITER)
def pending_vacancies_view(request):
    pending = VacancyRequest.objects.filter(status=VacancyRequest.Status.PENDING)
    return render(request, "job_postings/pending_vacancies.html", {"pending": pending})


# Security boundary: only an HR Recruiter can create job postings.
@role_required(User.Role.HR_RECRUITER)
def create_job_posting_view(request):
    vacancy_request = None
    from_vacancy_id = request.GET.get("from_vacancy") or request.POST.get("from_vacancy")
    if from_vacancy_id:
        vacancy_request = get_object_or_404(VacancyRequest, pk=from_vacancy_id)

    if request.method == "POST":
        form = JobPostingForm(request.POST)
        if form.is_valid():
            status = JobPosting.Status.PUBLISHED if "publish" in request.POST else JobPosting.Status.DRAFT
            posting = form.save(posted_by=request.user, vacancy_request=vacancy_request, status=status)
            if vacancy_request:
                vacancy_request.status = VacancyRequest.Status.POSTED
                vacancy_request.save(update_fields=["status"])
            messages.success(request, f"Job posting '{posting.job_title}' saved as {posting.get_status_display()}.")
            return redirect("job_posting_list")
    else:
        initial = {}
        if vacancy_request:
            initial = {
                "job_title": vacancy_request.job_title,
                "department": vacancy_request.department,
                "num_openings": vacancy_request.num_openings,
                "key_responsibilities": vacancy_request.description,
            }
        form = JobPostingForm(initial=initial)

    past_titles = list(
        JobPosting.objects.exclude(job_title="").values_list("job_title", flat=True).distinct()
    )

    return render(
        request,
        "job_postings/job_posting_form.html",
        {"form": form, "vacancy_request": vacancy_request, "past_titles": past_titles},
    )


@role_required(User.Role.HR_RECRUITER)
def edit_job_posting_view(request, pk):
    posting = get_object_or_404(JobPosting, pk=pk)
    # Ownership boundary: only the recruiter who posted this specific opening may edit it.
    if posting.posted_by != request.user:
        raise PermissionDenied("You can only edit your own job postings.")

    if request.method == "POST":
        form = JobPostingForm(request.POST)
        if form.is_valid():
            status = JobPosting.Status.PUBLISHED if "publish" in request.POST else posting.status
            form.save(posted_by=request.user, status=status, instance=posting)
            messages.success(request, f"Job posting '{posting.job_title}' updated.")
            return redirect("job_posting_list")
    else:
        form = JobPostingForm(initial={
            "job_title": posting.job_title,
            "department": posting.department,
            "employment_type": posting.employment_type,
            "work_mode": posting.work_mode,
            "num_openings": posting.num_openings,
            "location": posting.location,
            "required_skills": ",".join(posting.required_skills.values_list("name", flat=True)),
            "nice_to_have_skills": ",".join(posting.nice_to_have_skills.values_list("name", flat=True)),
            "min_experience_years": posting.min_experience_years,
            "education_level": posting.education_level,
            "key_responsibilities": posting.key_responsibilities,
            "reporting_to": posting.reporting_to_id,
            "salary_range": posting.salary_range,
            "closing_date": posting.closing_date,
            "internal_notes": posting.internal_notes,
        })

    past_titles = list(
        JobPosting.objects.exclude(job_title="").values_list("job_title", flat=True).distinct()
    )

    return render(
        request,
        "job_postings/job_posting_form.html",
        {"form": form, "vacancy_request": posting.vacancy_request, "past_titles": past_titles, "editing": posting},
    )


@role_required(User.Role.HR_RECRUITER)
def job_posting_list_view(request):
    # Ownership boundary: a recruiter only manages the postings they personally created,
    # not every posting created by anyone holding the HR Recruiter role.
    postings = JobPosting.objects.filter(posted_by=request.user)
    return render(request, "job_postings/job_posting_list.html", {"postings": postings})


@role_required(User.Role.HR_RECRUITER)
def transition_job_posting_view(request, pk, action):
    posting = get_object_or_404(JobPosting, pk=pk)
    # Ownership boundary: only the recruiter who posted this opening may change its status.
    if posting.posted_by != request.user:
        raise PermissionDenied("You can only manage your own job postings.")
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")

    if action == "publish" and posting.status == JobPosting.Status.DRAFT:
        posting.status = JobPosting.Status.PUBLISHED
    elif action == "close" and posting.status == JobPosting.Status.PUBLISHED:
        posting.status = JobPosting.Status.CLOSED
    else:
        raise PermissionDenied("Invalid status transition.")

    posting.save(update_fields=["status"])
    messages.success(request, f"'{posting.job_title}' is now {posting.get_status_display()}.")
    return redirect("job_posting_list")


@role_required(User.Role.HR_RECRUITER)
def duplicate_job_posting_view(request, pk):
    original = get_object_or_404(JobPosting, pk=pk)
    # Ownership boundary: only the recruiter who posted this opening may duplicate it.
    if original.posted_by != request.user:
        raise PermissionDenied("You can only duplicate your own job postings.")
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")

    copy = JobPosting.objects.create(
        vacancy_request=original.vacancy_request,
        job_title=f"{original.job_title} (Copy)",
        department=original.department,
        employment_type=original.employment_type,
        work_mode=original.work_mode,
        num_openings=original.num_openings,
        location=original.location,
        min_experience_years=original.min_experience_years,
        education_level=original.education_level,
        key_responsibilities=original.key_responsibilities,
        reporting_to=original.reporting_to,
        salary_range=original.salary_range,
        closing_date=original.closing_date,
        internal_notes=original.internal_notes,
        status=JobPosting.Status.DRAFT,
        posted_by=request.user,
    )
    copy.required_skills.set(original.required_skills.all())
    copy.nice_to_have_skills.set(original.nice_to_have_skills.all())
    messages.success(request, f"Duplicated as '{copy.job_title}' (Draft).")
    return redirect("job_posting_list")


@role_required(User.Role.HR_RECRUITER)
def delete_job_posting_view(request, pk):
    posting = get_object_or_404(JobPosting, pk=pk)
    # Ownership boundary: only the recruiter who posted this opening may delete it.
    if posting.posted_by != request.user:
        raise PermissionDenied("You can only delete your own job postings.")
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    posting.delete()
    messages.success(request, "Job posting deleted.")
    return redirect("job_posting_list")


# ---------------------------------------------------------------------------
# Candidate — browse & apply
# ---------------------------------------------------------------------------

# Security boundary: only a Candidate can browse the job site / apply.
@role_required(User.Role.CANDIDATE)
def browse_jobs_view(request):
    # Never show Draft/Closed postings to candidates.
    postings = JobPosting.objects.filter(status=JobPosting.Status.PUBLISHED)

    query = request.GET.get("q", "").strip()
    department = request.GET.get("department", "").strip()
    location = request.GET.get("location", "").strip()

    if query:
        postings = postings.filter(
            models.Q(job_title__icontains=query) | models.Q(key_responsibilities__icontains=query)
        )
    if department:
        postings = postings.filter(department=department)
    if location:
        postings = postings.filter(location__icontains=location)

    return render(request, "job_postings/browse_jobs.html", {
        "postings": postings,
        "departments": Department.choices,
        "query": query,
        "selected_department": department,
        "location": location,
    })


@role_required(User.Role.CANDIDATE)
def job_detail_view(request, pk):
    posting = get_object_or_404(JobPosting, pk=pk, status=JobPosting.Status.PUBLISHED)
    already_applied = Application.objects.filter(job_posting=posting, candidate=request.user).exists()
    return render(request, "job_postings/job_detail.html", {"posting": posting, "already_applied": already_applied})


@role_required(User.Role.CANDIDATE)
def apply_job_view(request, pk):
    posting = get_object_or_404(JobPosting, pk=pk, status=JobPosting.Status.PUBLISHED)

    # Data consistency: a candidate may not apply twice to the same job posting.
    if Application.objects.filter(job_posting=posting, candidate=request.user).exists():
        messages.info(request, "You've already applied to this job.")
        return redirect("job_detail", pk=posting.pk)

    profile_resume = getattr(getattr(request.user, "candidate_profile", None), "resume", None)

    if request.method == "POST":
        form = ApplicationForm(request.POST, request.FILES, posting=posting)
        if form.is_valid():
            resume = form.cleaned_data["resume"] or profile_resume
            if not resume:
                form.add_error("resume", "Upload a resume, or add one to your profile first.")
            else:
                try:
                    Application.objects.create(
                        job_posting=posting,
                        candidate=request.user,  # never taken from the form
                        resume=resume,
                        sop_why_role=form.cleaned_data["sop_why_role"],
                        sop_why_company=form.cleaned_data["sop_why_company"],
                        expected_salary=form.cleaned_data["expected_salary"],
                        soft_skills=form.cleaned_data["soft_skills"],
                        work_authorized=form.cleaned_data["work_authorized"] == "YES",
                        requires_sponsorship=form.cleaned_data["requires_sponsorship"] == "YES",
                        notice_period=form.cleaned_data["notice_period"],
                        willing_to_relocate=(
                            form.cleaned_data.get("willing_to_relocate") == "YES"
                            if "willing_to_relocate" in form.cleaned_data else None
                        ),
                        linkedin_url=form.cleaned_data["linkedin_url"],
                        portfolio_url=form.cleaned_data["portfolio_url"],
                        referral_source=form.cleaned_data["referral_source"],
                        referred_by=form.cleaned_data["referred_by"],
                    )
                except IntegrityError:
                    # Data consistency, enforced at the DB level (UniqueConstraint) too -
                    # this catches a double-submit race the pre-check above can miss.
                    messages.info(request, "You've already applied to this job.")
                    return redirect("job_detail", pk=posting.pk)
                messages.success(request, f"Application submitted for {posting.job_title}.")
                return redirect("my_applications")
    else:
        form = ApplicationForm(posting=posting)

    return render(request, "job_postings/apply_job.html", {
        "form": form,
        "posting": posting,
        "has_profile_resume": bool(profile_resume),
        "profile_resume_name": profile_resume.name.rsplit("/", 1)[-1] if profile_resume else "",
    })


@role_required(User.Role.CANDIDATE)
def my_applications_view(request):
    # Ownership boundary: a candidate only ever sees their own applications.
    applications = Application.objects.filter(candidate=request.user).select_related("job_posting")
    return render(request, "job_postings/my_applications.html", {"applications": applications})


# ---------------------------------------------------------------------------
# Interviewer — visibility into applicants
# ---------------------------------------------------------------------------

# Security boundary: only an Interviewer sees applicant lists.
@role_required(User.Role.INTERVIEWER)
def interviewer_dashboard_view(request):
    postings = (
        JobPosting.objects.filter(applications__status__in=[Application.Status.APPLIED, Application.Status.SHORTLISTED])
        .distinct()
        .prefetch_related("applications__candidate")
    )
    return render(request, "job_postings/interviewer_dashboard.html", {"postings": postings})


@role_required(User.Role.INTERVIEWER)
def schedule_interview_stub_view(request, pk):
    application = get_object_or_404(Application, pk=pk)
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")
    # Stub: interview scheduling mechanics (calendar, Google Meet link) are a later module.
    messages.info(request, f"Interview scheduling for {application.candidate.full_name} is coming in a future module.")
    return redirect("interviewer_dashboard")
