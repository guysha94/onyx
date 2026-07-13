"""Unit tests for the Miro exact-match identifier detection in
ee.onyx.search.process_search_query.

FORK: miro
"""

from ee.onyx.search.process_search_query import _detect_miro_identifier_tag


class TestDetectMiroIdentifierTag:
    def test_asset_filename_hash_is_detected(self) -> None:
        tag = _detect_miro_identifier_tag("1ab6ca7c7db07f88e3040413c198bf2b.jpg")

        assert tag is not None
        assert tag.tag_key == "asset_filename"
        assert tag.tag_value == "1ab6ca7c7db07f88e3040413c198bf2b.jpg"

    def test_asset_filename_is_case_insensitive_on_extension(self) -> None:
        tag = _detect_miro_identifier_tag("1ab6ca7c7db07f88e3040413c198bf2b.PNG")

        assert tag is not None
        assert tag.tag_key == "asset_filename"

    def test_generic_filename_with_number_suffix_is_detected(self) -> None:
        # "image_720.png" is a generic name but still a valid exact-match key.
        tag = _detect_miro_identifier_tag("image_720.png")

        assert tag is not None
        assert tag.tag_key == "asset_filename"
        assert tag.tag_value == "image_720.png"

    def test_human_named_filename_is_detected(self) -> None:
        tag = _detect_miro_identifier_tag("Logo-Final.webp")

        assert tag is not None
        assert tag.tag_key == "asset_filename"
        assert tag.tag_value == "Logo-Final.webp"

    def test_filename_with_spaces_is_not_detected(self) -> None:
        # Multi-word query — must fall through to normal hybrid search.
        assert _detect_miro_identifier_tag("my logo final.png") is None

    def test_non_image_extension_is_not_detected_as_filename(self) -> None:
        # ".txt" is not a Miro image type.
        assert _detect_miro_identifier_tag("notes.txt") is None

    def test_full_doc_id_extracts_item_id(self) -> None:
        tag = _detect_miro_identifier_tag("miro__board1__item1")

        assert tag is not None
        assert tag.tag_key == "miro_item_id"
        assert tag.tag_value == "item1"

    def test_full_doc_id_with_underscores_in_segments_uses_last_segment(
        self,
    ) -> None:
        # board_id/item_id may themselves contain underscores; the item id is
        # reliably whatever follows the last "__" separator.
        tag = _detect_miro_identifier_tag("miro__board_1__item_1")

        assert tag is not None
        assert tag.tag_key == "miro_item_id"
        assert tag.tag_value == "item_1"

    def test_doc_id_prefix_without_separator_is_not_detected(self) -> None:
        assert _detect_miro_identifier_tag("miro__onlyoneseg") is None

    def test_bare_item_id_with_digit_is_detected(self) -> None:
        tag = _detect_miro_identifier_tag("uXjVH_7LB9o=")

        assert tag is not None
        assert tag.tag_key == "miro_item_id"
        assert tag.tag_value == "uXjVH_7LB9o="

    def test_short_bare_token_is_not_detected(self) -> None:
        # Below the length floor even though it contains a digit.
        assert _detect_miro_identifier_tag("ab12") is None

    def test_free_text_without_digits_is_not_detected(self) -> None:
        assert _detect_miro_identifier_tag("wheel") is None

    def test_free_text_with_whitespace_is_not_detected(self) -> None:
        assert _detect_miro_identifier_tag("dice wheel charge") is None

    def test_empty_query_is_not_detected(self) -> None:
        assert _detect_miro_identifier_tag("") is None
        assert _detect_miro_identifier_tag("   ") is None
