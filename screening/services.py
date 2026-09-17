"""
Resume scoring engine. Pure, reusable functions - never call scoring logic
inline from a view.

Fully offline: rule-based normalization + rapidfuzz string similarity. No
external AI/LLM calls anywhere in this module.

Source of truth for candidate skills is CandidateProfile.skills (already parsed
at signup). This module never re-reads or re-parses the raw resume file.
"""

import re
from datetime import date

from django.utils import timezone
from rapidfuzz import fuzz

from .constants import (
    DEGREE_KEYWORD_RANK,
    EDUCATION_LEVEL_RANK,
    FUZZY_MATCH_THRESHOLD,
    LOW_SIGNAL_SKILL_COUNT,
    NICE_TO_HAVE_WEIGHT,
    REQUIRED_SKILL_WEIGHT,
    SKILL_ALIASES,
    TIER_POTENTIAL,
    TIER_POTENTIAL_MIN,
    TIER_STRONG,
    TIER_STRONG_MIN,
    TIER_WEAK,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNKNOWN,
)

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
ONGOING_WORDS = ("present", "current", "now", "ongoing", "till date")


# ---------------------------------------------------------------------------
# Skill matching
# ---------------------------------------------------------------------------

def normalize_skill(name):
    """Lowercase, strip, collapse whitespace, then apply the alias map."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower())
    return SKILL_ALIASES.get(cleaned, cleaned)


def _best_match(job_skill, candidate_skills):
    """Finds the closest candidate skill for one job skill.

    Returns (candidate_skill, similarity) or (None, 0) when nothing clears
    FUZZY_MATCH_THRESHOLD. Exact normalized hits short-circuit at 100.
    """
    target = normalize_skill(job_skill)
    if not target:
        return None, 0

    best_skill, best_score = None, 0
    for candidate_skill in candidate_skills:
        other = normalize_skill(candidate_skill)
        if not other:
            continue
        if other == target:
            return candidate_skill, 100
        # ratio catches typos; token_sort_ratio catches reordered multi-word
        # names ("web development" vs "development, web"). Deliberately NOT
        # partial_ratio/WRatio - those would match "java" inside "javascript".
        similarity = max(fuzz.ratio(target, other), fuzz.token_sort_ratio(target, other))
        if similarity > best_score:
            best_skill, best_score = candidate_skill, similarity

    if best_score >= FUZZY_MATCH_THRESHOLD:
        return best_skill, int(best_score)
    return None, 0


def match_skill_set(job_skills, candidate_skills):
    """Matches one list of job skills against the candidate's skills.

    Returns (matched, missing) where each matched entry records both sides of
    the match so the UI can explain exactly why a skill counted.
    """
    matched, missing = [], []
    for job_skill in job_skills:
        candidate_skill, similarity = _best_match(job_skill, candidate_skills)
        if candidate_skill is not None:
            matched.append({
                "job_skill": job_skill,
                "candidate_skill": candidate_skill,
                "similarity": similarity,
            })
        else:
            missing.append(job_skill)
    return matched, missing


# ---------------------------------------------------------------------------
# Experience / education badges (reported separately, never folded into score)
# ---------------------------------------------------------------------------

def _extract_year(value):
    match = YEAR_RE.search(value or "")
    return int(match.group(0)) if match else None


def estimate_experience_years(profile):
    """Rough total years from parsed ExperienceEntry rows.

    Returns None when nothing is parseable - the resume parser deliberately
    leaves fields blank rather than guessing, so "unknown" must stay distinct
    from "zero years". Overlapping roles are summed rather than merged; this is
    an approximation and the recruiter sees the number to sanity-check it.
    """
    total = 0.0
    found_any = False
    for entry in profile.experience_entries.all():
        start_year = _extract_year(entry.start_date)
        if start_year is None:
            continue
        end_text = (entry.end_date or "").lower()
        if any(word in end_text for word in ONGOING_WORDS):
            end_year = date.today().year
        else:
            end_year = _extract_year(entry.end_date)
        if end_year is None:
            continue
        span = end_year - start_year
        if span >= 0:
            total += span
            found_any = True
    return total if found_any else None


def evaluate_experience(profile, posting):
    """Pass/fail/unknown badge for experience vs the posting's minimum."""
    required = posting.min_experience_years or 0
    if required == 0:
        return {"verdict": VERDICT_PASS, "years": estimate_experience_years(profile), "required": 0}

    years = estimate_experience_years(profile)
    if years is None:
        return {"verdict": VERDICT_UNKNOWN, "years": None, "required": required}
    return {
        "verdict": VERDICT_PASS if years >= required else VERDICT_FAIL,
        "years": years,
        "required": required,
    }


def _degree_rank(degree_text):
    text = (degree_text or "").strip().lower()
    if not text:
        return None
    for keyword, rank in DEGREE_KEYWORD_RANK:
        if keyword in text:
            return rank
    return None


def highest_education_rank(profile):
    ranks = [r for r in (_degree_rank(e.degree) for e in profile.education_entries.all()) if r is not None]
    return max(ranks) if ranks else None


def evaluate_education(profile, posting):
    """Pass/fail/unknown badge for education vs the posting's required level."""
    required_rank = EDUCATION_LEVEL_RANK.get(posting.education_level, 0)
    if required_rank == 0:
        return {"verdict": VERDICT_PASS, "required": posting.get_education_level_display()}

    actual_rank = highest_education_rank(profile)
    if actual_rank is None:
        return {"verdict": VERDICT_UNKNOWN, "required": posting.get_education_level_display()}
    return {
        "verdict": VERDICT_PASS if actual_rank >= required_rank else VERDICT_FAIL,
        "required": posting.get_education_level_display(),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def fit_tier_for(score):
    if score >= TIER_STRONG_MIN:
        return TIER_STRONG
    if score >= TIER_POTENTIAL_MIN:
        return TIER_POTENTIAL
    return TIER_WEAK


def compute_score(job_skills_required, job_skills_nice, candidate_skills):
    """The scoring formula, isolated from the ORM so it's trivially testable.

    Returns a dict; `incomplete_job` signals the posting has no required skills
    (a job-config problem, not a candidate problem) rather than crashing.
    """
    matched_required, missing_required = match_skill_set(job_skills_required, candidate_skills)
    matched_nice, _ = match_skill_set(job_skills_nice, candidate_skills)

    total_required = len(job_skills_required)
    total_nice = len(job_skills_nice)

    if total_required == 0:
        # Nothing to score against - surface it as a posting configuration gap.
        return {
            "score": 0.0,
            "matched": matched_required,
            "missing": missing_required,
            "bonus": matched_nice,
            "incomplete_job": True,
        }

    required_ratio = len(matched_required) / total_required
    if total_nice == 0:
        # No nice-to-haves defined, so required skills carry the full 100.
        score = required_ratio * (REQUIRED_SKILL_WEIGHT + NICE_TO_HAVE_WEIGHT)
    else:
        nice_ratio = len(matched_nice) / total_nice
        score = required_ratio * REQUIRED_SKILL_WEIGHT + nice_ratio * NICE_TO_HAVE_WEIGHT

    return {
        "score": round(score, 1),
        "matched": matched_required,
        "missing": missing_required,
        "bonus": matched_nice,
        "incomplete_job": False,
    }


def score_application(application, *, commit=True):
    """Scores one application against its posting and persists the result.

    Safe to call repeatedly (recompute is idempotent). A candidate with no
    profile or no parsed skills scores 0 but is flagged low_signal so a recruiter
    reviews it manually instead of trusting the number.
    """
    posting = application.job_posting
    profile = getattr(application.candidate, "candidate_profile", None)

    candidate_skills = list(profile.skills.values_list("name", flat=True)) if profile else []
    required = list(posting.required_skills.values_list("name", flat=True))
    nice = list(posting.nice_to_have_skills.values_list("name", flat=True))

    result = compute_score(required, nice, candidate_skills)

    application.match_score = result["score"]
    application.matched_skills = result["matched"]
    application.missing_skills = result["missing"]
    application.bonus_skills = result["bonus"]
    application.fit_tier = fit_tier_for(result["score"])
    application.low_signal = len(candidate_skills) < LOW_SIGNAL_SKILL_COUNT
    application.scored_at = timezone.now()

    if commit:
        application.save(update_fields=[
            "match_score", "matched_skills", "missing_skills", "bonus_skills",
            "fit_tier", "low_signal", "scored_at",
        ])
    return application


def recompute_for_posting(posting):
    """Rescores every application on a posting. Used by the recruiter's manual
    'Recompute scores' action and by the skills-changed signal."""
    applications = posting.applications.select_related("candidate").all()
    for application in applications:
        score_application(application)
    return len(applications)
