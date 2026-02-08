from django.core.exceptions import ValidationError
from django.test import TestCase

from authn.options import (
    DEFAULT_ALLOWED_UPLOAD_MIME_TYPES,
    validate_allowed_upload_mime_types,
    validate_docling_options,
)


class TestDoclingOptions(TestCase):
    def test_allows_empty(self):
        validate_docling_options(None)
        validate_docling_options({})

    def test_requires_object(self):
        with self.assertRaises(ValidationError):
            validate_docling_options(["bad"])

    def test_validates_max_num_pages(self):
        with self.assertRaises(ValidationError):
            validate_docling_options({"max_num_pages": -1})
        with self.assertRaises(ValidationError):
            validate_docling_options({"max_num_pages": "ten"})

    def test_validates_max_file_size(self):
        with self.assertRaises(ValidationError):
            validate_docling_options({"max_file_size": -5})

    def test_validates_exports(self):
        with self.assertRaises(ValidationError):
            validate_docling_options({"exports": "markdown"})
        with self.assertRaises(ValidationError):
            validate_docling_options({"exports": [1, 2]})

    def test_validates_ocr(self):
        with self.assertRaises(ValidationError):
            validate_docling_options({"ocr": "yes"})

    def test_validates_ocr_languages(self):
        with self.assertRaises(ValidationError):
            validate_docling_options({"ocr_languages": "en"})
        with self.assertRaises(ValidationError):
            validate_docling_options({"ocr_languages": [1, "en"]})


class TestAllowedUploadMimeTypes(TestCase):
    def test_defaults_when_none(self):
        result = validate_allowed_upload_mime_types(None)
        self.assertEqual(result, list(DEFAULT_ALLOWED_UPLOAD_MIME_TYPES))

    def test_requires_list_of_strings(self):
        with self.assertRaises(ValidationError):
            validate_allowed_upload_mime_types("application/pdf")
        with self.assertRaises(ValidationError):
            validate_allowed_upload_mime_types([1, "application/pdf"])

    def test_rejects_empty_list(self):
        with self.assertRaises(ValidationError):
            validate_allowed_upload_mime_types([])

    def test_normalizes_and_deduplicates(self):
        result = validate_allowed_upload_mime_types(
            [" Application/PDF ", "application/x-pdf", "application/pdf"]
        )
        self.assertEqual(result, ["application/pdf", "application/x-pdf"])

    def test_rejects_unsupported_types(self):
        with self.assertRaises(ValidationError):
            validate_allowed_upload_mime_types(["application/pdf", "image/png"])
