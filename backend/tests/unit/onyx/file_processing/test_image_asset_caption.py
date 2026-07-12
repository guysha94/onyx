"""Unit tests for the structured asset-caption path (title + description) used by
image-asset connectors such as Miro.

Covers:
1. Parsing the ``TITLE: ... / DESCRIPTION: ...`` format.
2. Robust fallback when a small model doesn't follow the format.
3. ``summarize_image_and_title_with_error_handling`` wiring and error handling.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from onyx.file_processing.image_summarization import _parse_title_and_summary
from onyx.file_processing.image_summarization import (
    summarize_image_and_title_with_error_handling,
)
from onyx.file_processing.image_summarization import UnsupportedImageFormatError

_MODULE = "onyx.file_processing.image_summarization"


class TestParseTitleAndSummary:
    def test_parses_title_and_description(self) -> None:
        raw = (
            "TITLE: Purple Achievement Badge Icon\n"
            "DESCRIPTION: A flat-vector badge with a white starburst on a purple "
            "circle, blue accent, and a small checkmark."
        )
        result = _parse_title_and_summary(raw)
        assert result.title == "Purple Achievement Badge Icon"
        assert result.summary.startswith("A flat-vector badge")
        assert "purple" in result.summary

    def test_multiline_description_is_joined(self) -> None:
        raw = (
            "TITLE: Sprint Planning Board\n"
            "DESCRIPTION: Line one.\n"
            "Line two continues the description."
        )
        result = _parse_title_and_summary(raw)
        assert result.title == "Sprint Planning Board"
        assert "Line one." in result.summary
        assert "Line two continues" in result.summary

    def test_case_insensitive_labels(self) -> None:
        raw = "title: Cozy Kitchen Scene\ndescription: A warm illustrated kitchen."
        result = _parse_title_and_summary(raw)
        assert result.title == "Cozy Kitchen Scene"
        assert result.summary == "A warm illustrated kitchen."

    def test_no_title_falls_back_to_whole_text(self) -> None:
        raw = "Just a plain description with no structured labels at all."
        result = _parse_title_and_summary(raw)
        assert result.title is None
        assert result.summary == raw

    def test_empty_title_treated_as_none(self) -> None:
        raw = "TITLE:\nDESCRIPTION: Only a description here."
        result = _parse_title_and_summary(raw)
        assert result.title is None
        assert result.summary == "Only a description here."


class TestSummarizeImageAndTitleWithErrorHandling:
    def test_returns_none_when_llm_missing(self) -> None:
        assert (
            summarize_image_and_title_with_error_handling(
                llm=None, image_data=b"x", context_name="a.png"
            )
            is None
        )

    def test_parses_pipeline_output(self) -> None:
        mock_llm = MagicMock()
        with patch(
            f"{_MODULE}.summarize_image_pipeline",
            return_value="TITLE: Hero Banner\nDESCRIPTION: A bold banner.",
        ):
            result = summarize_image_and_title_with_error_handling(
                llm=mock_llm, image_data=b"bytes", context_name="image.png"
            )
        assert result is not None
        assert result.title == "Hero Banner"
        assert result.summary == "A bold banner."

    def test_unsupported_format_returns_none(self) -> None:
        mock_llm = MagicMock()
        with patch(
            f"{_MODULE}.summarize_image_pipeline",
            side_effect=UnsupportedImageFormatError("bad"),
        ):
            result = summarize_image_and_title_with_error_handling(
                llm=mock_llm, image_data=b"bytes", context_name="image.tiff"
            )
        assert result is None
