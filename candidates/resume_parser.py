"""
Offline, rule-based resume parser. No external AI/LLM calls - text extraction
(pdfminer.six / python-docx) plus regex pattern matching and a lightweight,
keyword-and-position heuristic entity spotter for institute/company names,
each scoped strictly to its own resume section to avoid false positives.

Accuracy over cleverness: every extractor below returns '' / [] rather than a
guess whenever it isn't confident - the caller (signup flow) always shows the
result in an editable review form before anything is saved.
"""

import io
import logging
import re

from job_postings.models import Skill

logger = logging.getLogger(__name__)

SECTION_HEADERS = {
    "education": ["education", "academic background", "academic qualifications", "educational qualifications"],
    "experience": ["experience", "work experience", "professional experience", "employment history"],
    "skills": ["skills", "technical skills", "core competencies", "key skills"],
}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{7,15}\d)")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
GRADE_RE = re.compile(r"(?:CGPA|GPA)\s*[:\-]?\s*([\d.]+(?:\s*/\s*[\d.]+)?)", re.IGNORECASE)
PERCENT_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?\s*%)")

DEGREE_KEYWORDS = [
    "B.Tech", "B.E.", "BE", "Bachelor of Technology", "Bachelor of Engineering",
    "B.Sc", "BSc", "Bachelor of Science", "B.A.", "BA", "Bachelor of Arts",
    "M.Tech", "M.E.", "Master of Technology", "M.Sc", "MSc", "Master of Science",
    "MBA", "Master of Business Administration", "M.A.", "MA", "Master of Arts",
    "PhD", "Ph.D", "Doctor of Philosophy", "Diploma", "BCA", "MCA",
]
# Word-boundary wrapped so short abbreviations like "BE"/"MA"/"BA" only match as a
# standalone word, never as a substring inside unrelated text (e.g. "Ma" in "Machine").
DEGREE_RE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in DEGREE_KEYWORDS) + r")\b", re.IGNORECASE)

INSTITUTE_KEYWORDS = ["university", "college", "institute", "school", "academy", "polytechnic"]
COMPANY_SUFFIX_KEYWORDS = [
    "inc", "ltd", "llc", "pvt", "private limited", "technologies", "systems",
    "solutions", "corp", "corporation", "labs", "consulting",
]

MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}"
DATE_RANGE_RE = re.compile(
    rf"({MONTH}|\d{{4}})\s*(?:-|–|—|to)\s*({MONTH}|\d{{4}}|present|current)", re.IGNORECASE
)


def extract_text(uploaded_file):
    """Plain text from an uploaded PDF/DOCX/TXT file. Returns '' for anything
    unsupported or unreadable - never raises, so a bad upload just skips extraction."""
    name = uploaded_file.name.lower()
    try:
        if name.endswith(".pdf"):
            from pdfminer.high_level import extract_text as pdf_extract_text
            uploaded_file.seek(0)
            # pdfminer's extract_text() only accepts a real path or a plain file-like
            # object - Django's UploadedFile wrapper is rejected outright (PDFTypeError),
            # so we copy the bytes into a bare BytesIO first.
            return pdf_extract_text(io.BytesIO(uploaded_file.read())) or ""
        if name.endswith(".docx"):
            import docx
            uploaded_file.seek(0)
            document = docx.Document(io.BytesIO(uploaded_file.read()))
            return "\n".join(p.text for p in document.paragraphs)
        if name.endswith(".txt"):
            uploaded_file.seek(0)
            return uploaded_file.read().decode("utf-8", errors="ignore")
    except Exception:
        logger.exception("Resume text extraction failed for %r", uploaded_file.name)
        return ""
    return ""


BULLET_CHARS = "•●▪‣◦-–*"


def _looks_like_header(line):
    """Generic fallback header detector: a short, all-caps standalone line (e.g.
    PROJECTS, CERTIFICATIONS, STRENGTHS) - the overwhelmingly common resume
    convention - even when we don't recognize its specific name. Without this,
    an unrecognized header would silently let its content bleed into whatever
    section came before it."""
    if line[:1] in BULLET_CHARS:
        return False  # a bulleted line is a list item, never a header
    words = line.split()
    if not (1 <= len(words) <= 5):
        return False
    letters = [c for c in line if c.isalpha()]
    if len(letters) < 3:
        return False  # too short to be a real header - avoids e.g. a lone "C" skill
    return line == line.upper()


def _segment_sections(text):
    """Splits resume text into named blocks by scanning for header-like lines.
    Everything before the first header goes to '_preamble'; content under a
    header we don't specifically track (e.g. Projects, Contact) goes to '_other'
    so it can never bleed into education/experience/skills."""
    sections = {"education": [], "experience": [], "skills": [], "_preamble": [], "_other": []}
    current = "_preamble"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        key = line.lower().strip(":")
        if key and len(key.split()) <= 4:
            matched = next((s for s, kws in SECTION_HEADERS.items() if key in kws), None)
            if matched:
                current = matched
                continue
        if _looks_like_header(line):
            current = "_other"
            continue
        sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _extract_email(text):
    match = EMAIL_RE.search(text)
    return match.group(0) if match else ""


def _extract_phone(text):
    match = PHONE_RE.search(text)
    if not match:
        return ""
    digits = re.sub(r"\D", "", match.group(0))
    return match.group(0).strip() if 7 <= len(digits) <= 15 else ""


def _extract_education_entries(section_text):
    """One entry per line containing a recognizable degree keyword; institute name
    only filled if a nearby line contains an institute keyword (never guessed)."""
    if not section_text:
        return []
    lines = [l for l in section_text.splitlines() if l.strip()]
    entries = []
    for idx, line in enumerate(lines):
        degree_match = DEGREE_RE.search(line)
        if not degree_match:
            continue
        window = lines[max(0, idx - 2): idx + 4]
        window_text = " ".join(window)

        institute = next((w.strip() for w in window if any(kw in w.lower() for kw in INSTITUTE_KEYWORDS)), "")

        # Handles both "B.Tech in Information Technology" and the equally common
        # comma-separated "B.Tech, Information Technology" phrasing.
        field_match = re.search(
            re.escape(degree_match.group(0)) + r"\.?\s*(?:in|of|,)\s+([A-Za-z&\-\s]{3,50})", line, re.IGNORECASE
        )
        field_of_study = field_match.group(1).strip() if field_match else ""

        years = YEAR_RE.findall(window_text)
        start_year = years[0] if years else ""
        end_year = years[1] if len(years) >= 2 else ("Present" if "present" in window_text.lower() else "")

        grade_match = GRADE_RE.search(window_text) or PERCENT_RE.search(window_text)
        grade = grade_match.group(0) if grade_match else ""

        entries.append({
            "institute_name": institute,
            "degree": degree_match.group(0),
            "field_of_study": field_of_study,
            "start_year": start_year,
            "end_year": end_year,
            "grade": grade,
        })
    return entries


def _extract_experience_entries(section_text):
    """One entry per line containing a date range; company name only filled if a
    nearby line contains a company-suffix keyword, job title only if the line
    immediately above the date reads like a short title (never guessed otherwise)."""
    if not section_text:
        return []
    lines = [l for l in section_text.splitlines() if l.strip()]
    entries = []
    for idx, line in enumerate(lines):
        date_match = DATE_RANGE_RE.search(line)
        if not date_match:
            continue
        window = lines[max(0, idx - 2): idx + 1]

        company = next((w.strip() for w in window if any(kw in w.lower() for kw in COMPANY_SUFFIX_KEYWORDS)), "")

        title = ""
        if idx - 1 >= 0:
            prev_line = lines[idx - 1].strip()
            if prev_line != company and 2 <= len(prev_line.split()) <= 6 and prev_line[:1].isupper():
                title = prev_line

        entries.append({
            "company_name": company,
            "job_title": title,
            "start_date": date_match.group(1),
            "end_date": date_match.group(2),
            "description": "",
        })
    return entries


def _extract_skills(section_text):
    """Only matches against the existing master Skill list - never invents a new
    skill from resume text. Returns [] (not a guess) if no Skills section was found."""
    if not section_text:
        return []
    lower_text = section_text.lower()
    matched = []
    for name in Skill.objects.values_list("name", flat=True):
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", lower_text):
            matched.append(name)
    return matched


def parse_resume(uploaded_file):
    """Returns a dict of extracted-but-unverified profile data for the signup
    review step. Every field is safe to leave blank - the candidate reviews all of it."""
    text = extract_text(uploaded_file)
    if not text.strip():
        return {"email": "", "phone": "", "education": [], "experience": [], "skills": []}

    sections = _segment_sections(text)
    return {
        "email": _extract_email(text),
        "phone": _extract_phone(text),
        "education": _extract_education_entries(sections.get("education", "")),
        "experience": _extract_experience_entries(sections.get("experience", "")),
        "skills": _extract_skills(sections.get("skills", "")),
    }
