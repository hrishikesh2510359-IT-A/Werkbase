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
