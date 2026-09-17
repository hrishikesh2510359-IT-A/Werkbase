from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from job_postings.models import JobPosting

from .services import recompute_for_posting

# Actions that mean the skill set actually changed on disk. `.set()` (used by
# JobPostingForm.save) fires remove+add, so this can run twice for one edit -
# harmless, since recompute is idempotent.
_RELEVANT_ACTIONS = {"post_add", "post_remove", "post_clear"}


def _handle_skill_change(instance, action, reverse):
    if action not in _RELEVANT_ACTIONS:
        return
    # reverse=True means someone edited from the Skill side; `instance` is then
    # a Skill, not a JobPosting, so there's no single posting to rescore here.
    if reverse or not isinstance(instance, JobPosting):
        return
    recompute_for_posting(instance)


@receiver(m2m_changed, sender=JobPosting.required_skills.through)
def required_skills_changed(sender, instance, action, reverse, **kwargs):
    """Rescore a posting's applications whenever its required skills change."""
    _handle_skill_change(instance, action, reverse)


@receiver(m2m_changed, sender=JobPosting.nice_to_have_skills.through)
def nice_to_have_skills_changed(sender, instance, action, reverse, **kwargs):
    """Same for nice-to-have skills, which feed the bonus portion of the score."""
    _handle_skill_change(instance, action, reverse)
