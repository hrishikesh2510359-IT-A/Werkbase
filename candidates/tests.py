"""Resume-parser tests: the parser must never crash on malformed input, and
should degrade to empty results rather than raising."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .resume_parser import extract_text, parse_resume

EMPTY = {"email": "", "phone": "", "education": [], "experience": [], "skills": []}


class ResumeParserRobustnessTests(TestCase):
    def test_garbage_pdf_does_not_crash(self):
        bad = SimpleUploadedFile("cv.pdf", b"%PDF-1.4 not really a pdf \x00\x01\x02", content_type="application/pdf")
        # Must return the empty shape, never raise.
        self.assertEqual(parse_resume(bad), EMPTY)

    def test_unknown_extension_returns_empty_text(self):
        weird = SimpleUploadedFile("cv.xyz", b"whatever", content_type="application/octet-stream")
        self.assertEqual(extract_text(weird), "")

    def test_empty_file_does_not_crash(self):
        empty = SimpleUploadedFile("cv.txt", b"", content_type="text/plain")
        self.assertEqual(parse_resume(empty), EMPTY)

    def test_plain_text_resume_extracts_email_and_phone(self):
        content = b"Jane Doe\njane@example.com\n+91 9876543210\n\nEDUCATION\nB.Tech in Computer Science\n"
        txt = SimpleUploadedFile("cv.txt", content, content_type="text/plain")
        result = parse_resume(txt)
        self.assertEqual(result["email"], "jane@example.com")
        self.assertIn("9876543210", result["phone"])

    def test_docx_garbage_does_not_crash(self):
        bad = SimpleUploadedFile("cv.docx", b"not a real docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(parse_resume(bad), EMPTY)


# ---------------------------------------------------------------------------
# AI resume reader (Gemini is always mocked - tests never hit the network)
# ---------------------------------------------------------------------------
import json  # noqa: E402
from unittest import mock  # noqa: E402

from django.test import override_settings  # noqa: E402

from job_postings.models import Skill  # noqa: E402

from . import ai_resume_parser  # noqa: E402

RESUME_TEXT = (
    "Jane Doe\njane@example.com | +91 98765 43210\n\nSKILLS\nPython, Django, PostgreSQL\n\n"
    "EDUCATION\nB.Tech in Computer Science, SSN College of Engineering 2021 - 2025 CGPA 8.9\n"
)


def _fake_response(payload):
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    return mock.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None)


@override_settings(GEMINI_API_KEY="test-key")
class AiResumeParserTests(TestCase):
    def setUp(self):
        for name in ("Python", "Django", "Java"):
            Skill.objects.get_or_create(name=name)
        self.upload = lambda: SimpleUploadedFile("cv.txt", RESUME_TEXT.encode(), content_type="text/plain")

    def _run(self, payload):
        with mock.patch("candidates.ai_resume_parser.requests.post", return_value=_fake_response(payload)) as post:
            return parse_resume(self.upload()), post

    def test_ai_result_is_used_and_normalised(self):
        result, post = self._run({
            "email": "jane@example.com", "phone": "+91 98765 43210",
            "education": [{"institute_name": "SSN College of Engineering", "degree": "B.Tech",
                           "field_of_study": "Computer Science", "start_year": "2021",
                           "end_year": "2025", "grade": "CGPA 8.9"}],
            "experience": [], "skills": ["python", "Django"],
        })
        post.assert_called_once()
        self.assertEqual(result["email"], "jane@example.com")
        self.assertEqual(result["skills"], ["Python", "Django"])  # canonical spelling
        self.assertEqual(result["education"][0]["degree"], "B.Tech")

    def test_hallucinated_values_are_dropped(self):
        result, _ = self._run({
            "email": "someone.else@example.com",     # not in the resume
            "phone": "+1 555 000 1111",               # not in the resume
            "education": [], "experience": [],
            "skills": ["Python", "Java", "Cobol"],   # Java not in text, Cobol not in master list
        })
        self.assertEqual(result["email"], "")
        self.assertEqual(result["phone"], "")
        self.assertEqual(result["skills"], ["Python"])

    def test_api_failure_falls_back_to_offline_parser(self):
        with mock.patch("candidates.ai_resume_parser.requests.post", side_effect=RuntimeError("boom")):
            result = parse_resume(self.upload())
        self.assertEqual(result["email"], "jane@example.com")  # regex parser still worked

    def test_pdf_is_sent_as_inline_document(self):
        parts = ai_resume_parser._build_parts(b"%PDF-1.4 x", "cv.pdf", "")
        self.assertEqual(parts[0]["inlineData"]["mimeType"], "application/pdf")

    @override_settings(GEMINI_API_KEY="")
    def test_no_key_means_ai_is_skipped(self):
        with mock.patch("candidates.ai_resume_parser.requests.post") as post:
            result = parse_resume(self.upload())
        post.assert_not_called()
        self.assertEqual(result["email"], "jane@example.com")
