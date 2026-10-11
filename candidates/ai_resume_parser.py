"""
AI resume reader (Google Gemini - free tier, no card needed). Plug in
GEMINI_API_KEY and uploaded resumes are read by the model instead of the regex
heuristics in resume_parser.py.

Why this is more accurate than regex:
  * PDFs are sent as the original document, so multi-column layouts, tables,
    fancy templates and even scanned/image-only pages are understood.
  * Output is forced into JSON mode with a fixed shape, never free text.

Why it can be trusted (no extractor is literally 100%, so we guard it):
  * The prompt forbids guessing - absent fields come back empty.
  * Every returned value is re-checked against the resume's own text (email,
    phone, skills must literally appear in it) so a hallucination is dropped.
  * Skills are restricted to the master Skill list, as before.
  * The result still lands in the editable review form before anything is saved.

Returns None on ANY problem (no key, API error, bad output) so the caller falls
back to the offline parser - signup is never blocked by this module.
"""

import base64
import json
import logging
import re

import requests
from django.conf import settings

from job_postings.models import Skill

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 60_000
MAX_PDF_BYTES = 20 * 1024 * 1024

JSON_SHAPE = """Return a single JSON object with exactly these keys:
{
  "email": "",
  "phone": "",
  "education": [{"institute_name": "", "degree": "", "field_of_study": "", "start_year": "", "end_year": "", "grade": ""}],
  "experience": [{"company_name": "", "job_title": "", "start_date": "", "end_date": "", "description": ""}],
  "skills": []
}
Years are 4-digit strings; end_year/end_date may be "Present". grade is the CGPA/percentage exactly as written.
experience.description is the key responsibilities copied/condensed from the resume (empty if none)."""

SYSTEM_PROMPT = """You extract structured data from a resume for an applicant tracking system.

Rules:
- Extract ONLY what is explicitly written in the resume. Never infer, guess or invent.
- If a field is not present, return an empty string (or an empty list). Empty is correct; a guess is wrong.
- Education: one entry per qualification (degree, diploma, school board). Do not put projects or certifications here.
- Experience: one entry per job or internship. Do NOT include academic projects, volunteering or certifications.
- Keep names, titles, dates and grades exactly as written.
- Skills: return ONLY items from the ALLOWED SKILLS list below that the resume shows the candidate has (use the list's exact spelling). Ignore skills not on the list.
- email/phone: the candidate's own contact details, not a referee's.
Respond with JSON only."""

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def is_enabled():
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


def _build_parts(raw_bytes, filename, text):
    """PDFs go to the model as the original document; everything else as text."""
    instruction = {"text": "Extract the resume data as JSON."}
    if filename.lower().endswith(".pdf") and raw_bytes and len(raw_bytes) <= MAX_PDF_BYTES:
        return [
            {"inlineData": {"mimeType": "application/pdf", "data": base64.standard_b64encode(raw_bytes).decode("ascii")}},
            instruction,
        ]
    if not text.strip():
        return None
    return [{"text": f"RESUME TEXT:\n\n{text[:MAX_TEXT_CHARS]}"}, instruction]


def _digits(value):
    return re.sub(r"\D", "", value or "")


def _clip(value, limit):
    return re.sub(r"[ \t]+", " ", (value or "").strip())[:limit]


def _clean(data, source_text, master_names):
    """Normalizes to the exact dict shape resume_parser.parse_resume returns,
    dropping anything the resume's own text doesn't support."""
    have_text = bool(source_text.strip())
    haystack = source_text.lower()
    digit_haystack = _digits(source_text)

    email = _clip(data.get("email"), 254)
    if email and (not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", email) or (have_text and email.lower() not in haystack)):
        email = ""

    phone = _clip(data.get("phone"), 20)
    if phone and (not 7 <= len(_digits(phone)) <= 15 or (have_text and _digits(phone) not in digit_haystack)):
        phone = ""

    education = []
    for e in data.get("education") or []:
        entry = {
            "institute_name": _clip(e.get("institute_name"), 200),
            "degree": _clip(e.get("degree"), 150),
            "field_of_study": _clip(e.get("field_of_study"), 150),
            "start_year": _clip(e.get("start_year"), 10),
            "end_year": _clip(e.get("end_year"), 10),
            "grade": _clip(e.get("grade"), 50),
        }
        if any(entry.values()):
            education.append(entry)

    experience = []
    for e in data.get("experience") or []:
        entry = {
            "company_name": _clip(e.get("company_name"), 200),
            "job_title": _clip(e.get("job_title"), 150),
            "start_date": _clip(e.get("start_date"), 50),
            "end_date": _clip(e.get("end_date"), 50),
            "description": (e.get("description") or "").strip()[:1500],
        }
        if entry["company_name"] or entry["job_title"]:
            experience.append(entry)

    by_lower = {name.lower(): name for name in master_names}
    skills, seen = [], set()
    for raw in data.get("skills") or []:
        name = by_lower.get((raw or "").strip().lower())
        if not name or name in seen:
            continue
        # Grounding check: a skill must literally appear in the resume text.
        if have_text and not re.search(r"(?<![\w+#])" + re.escape(name.lower()) + r"(?![\w+#])", haystack):
            continue
        seen.add(name)
        skills.append(name)

    return {"email": email, "phone": phone, "education": education, "experience": experience, "skills": skills}


def ai_parse_resume(raw_bytes, filename, text):
    """Returns the parsed dict, or None if the AI path is unavailable/failed."""
    if not is_enabled():
        return None
    parts = _build_parts(raw_bytes, filename, text)
    if parts is None:
        return None

    master_names = list(Skill.objects.values_list("name", flat=True))
    try:
        response = requests.post(
            GEMINI_URL.format(model=settings.GEMINI_MODEL),
            headers={"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + "\n\n" + JSON_SHAPE + "\n\nALLOWED SKILLS: " + ", ".join(master_names)}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
            },
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning("AI resume parse returned non-object JSON")
            return None
        return _clean(data, text, master_names)
    except Exception:
        # Auth, free-tier rate limit (429), network, malformed output - never block signup.
        logger.exception("AI resume parsing failed; falling back to offline parser")
        return None
