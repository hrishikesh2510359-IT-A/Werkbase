from django.core.management.base import BaseCommand

from accounts.models import User

SEED_PASSWORD = "Test@1234"

SEED_USERS = [
    ("hiring.manager@werkbase.com", "Hiring Manager", User.Role.HIRING_MANAGER),
    ("recruiter@werkbase.com", "HR Recruiter", User.Role.HR_RECRUITER),
    ("interviewer@werkbase.com", "Interviewer", User.Role.INTERVIEWER),
    ("hr.manager@werkbase.com", "HR Manager", User.Role.HR_MANAGER),
    ("onboarding@werkbase.com", "Onboarding Coordinator", User.Role.ONBOARDING_COORDINATOR),
    ("admin@werkbase.com", "HR Admin", User.Role.HR_ADMIN),
    ("candidate@werkbase.com", "Candidate", User.Role.CANDIDATE),
]


class Command(BaseCommand):
    help = "Seed 7 demo users, one per role"

    def handle(self, *args, **options):
        for email, full_name, role in SEED_USERS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"full_name": full_name, "role": role},
            )
            user.full_name = full_name
            user.role = role
            user.set_password(SEED_PASSWORD)
            user.save()
            status = "created" if created else "updated"
            self.stdout.write(self.style.SUCCESS(f"{status}: {email} ({role})"))
