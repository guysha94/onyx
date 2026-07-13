"""Unit tests for Monday exact-match identifier detection in
ee.onyx.search.process_search_query.

FORK: monday
"""

from ee.onyx.search.process_search_query import _detect_monday_identifier_tag


class TestDetectMondayIdentifierTag:
    def test_workspace_phrase_is_detected(self) -> None:
        tag = _detect_monday_identifier_tag("General Tasks in monday workspace 5485895")

        assert tag is not None
        assert tag.tag_key == "workspace_id"
        assert tag.tag_value == "5485895"

    def test_board_phrase_is_detected(self) -> None:
        tag = _detect_monday_identifier_tag("items on board 6302069973")

        assert tag is not None
        assert tag.tag_key == "board_id"
        assert tag.tag_value == "6302069973"

    def test_board_url_is_detected(self) -> None:
        tag = _detect_monday_identifier_tag(
            "https://superplay.monday.com/boards/6302069973/pulses/7291287222"
        )

        assert tag is not None
        assert tag.tag_key == "board_id"
        assert tag.tag_value == "6302069973"

    def test_bare_numeric_workspace_id_is_detected(self) -> None:
        tag = _detect_monday_identifier_tag("5485895")

        assert tag is not None
        assert tag.tag_key == "workspace_id"
        assert tag.tag_value == "5485895"

    def test_free_text_is_not_detected(self) -> None:
        assert (
            _detect_monday_identifier_tag("Competitor game update newsletter") is None
        )
