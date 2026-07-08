from datetime import datetime
from datetime import timezone
from typing import Any
from unittest.mock import patch

from onyx.configs.constants import DocumentSource
from onyx.connectors.miro.connector import _board_deep_link
from onyx.connectors.miro.connector import _build_asset_title
from onyx.connectors.miro.connector import _filename_from_content_disposition
from onyx.connectors.miro.connector import _filename_from_url
from onyx.connectors.miro.connector import _is_meaningful_filename
from onyx.connectors.miro.connector import _strip_html
from onyx.connectors.miro.connector import MiroConnector
from onyx.connectors.models import ImageSection
from onyx.connectors.models import TextSection


def _image_item(
    item_id: str,
    image_url: str = "https://api.miro.com/v2/boards/board1/items/abc?format=preview",
    title: str | None = None,
    parent_id: str | None = None,
    modified_at: str | None = "2024-01-15T12:00:00Z",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "type": "image",
        "data": {"imageUrl": image_url, "title": title},
        "modifiedAt": modified_at,
    }
    if parent_id:
        item["parent"] = {"id": parent_id}
    return item


def _frame_item(item_id: str, title: str) -> dict[str, Any]:
    return {"id": item_id, "type": "frame", "data": {"title": title}}


def _text_item(
    item_id: str, content: str, parent_id: str | None, item_type: str = "text"
) -> dict[str, Any]:
    item: dict[str, Any] = {"id": item_id, "type": item_type, "data": {"content": content}}
    if parent_id:
        item["parent"] = {"id": parent_id}
    return item


def test_strip_html_removes_tags_and_unescapes_entities() -> None:
    assert _strip_html("<p>Login &amp; Signup</p>") == "Login & Signup"
    assert _strip_html(None) == ""
    assert _strip_html("") == ""


def test_filename_from_content_disposition_variants() -> None:
    assert (
        _filename_from_content_disposition('attachment; filename="hero.png"')
        == "hero.png"
    )
    assert (
        _filename_from_content_disposition("attachment; filename=hero.png")
        == "hero.png"
    )
    assert (
        _filename_from_content_disposition("attachment; filename*=UTF-8''hero.png")
        == "hero.png"
    )
    assert _filename_from_content_disposition(None) is None
    assert _filename_from_content_disposition("attachment") is None


def test_filename_from_url_extracts_basename() -> None:
    assert (
        _filename_from_url("https://cdn.miro.com/assets/1ab6ca7c7db.jpg?x=1")
        == "1ab6ca7c7db.jpg"
    )
    assert _filename_from_url("https://cdn.miro.com/assets/") is None


def test_is_meaningful_filename_rejects_generic_placeholders() -> None:
    for generic in [
        None,
        "",
        "image.png",
        "image.jpg",
        "img.png",
        "IMG_1234.jpg",
        "image-2.png",
        "download.png",
        "download (1).jpeg",
        "screenshot.png",
        "screen shot.png",
        "untitled.png",
        "unnamed.png",
        "frame.png",
        "asset.png",
    ]:
        assert _is_meaningful_filename(generic) is False, generic

    for real in [
        "hero-banner.png",
        "checkout_button.svg",
        "boss_fight_concept.jpg",
        "1ab6ca7c7db.jpg",
    ]:
        assert _is_meaningful_filename(real) is True, real


def test_is_meaningful_filename_rejects_numeric_and_too_short_stems() -> None:
    """Bare numeric/short stems (e.g. auto-generated download counters) carry
    no semantic meaning and must never become a document title."""
    for placeholder in [
        "4.png",
        "12.jpg",
        "3458764677369940790.png",
        "a.png",
        "ab.png",
        "1.svg",
    ]:
        assert _is_meaningful_filename(placeholder) is False, placeholder

    # A three-char alphanumeric stem that isn't purely numeric is still
    # meaningful — only bare-number stems and stems of 2 chars or fewer are
    # rejected.
    assert _is_meaningful_filename("ok2.png") is True


def test_build_asset_title_prefers_real_title_then_meaningful_filename() -> None:
    # explicit asset title wins
    assert (
        _build_asset_title("Hero Banner", "image.png", "Frame", "Board", None, "abc123")
        == "Hero Banner"
    )
    # meaningful filename beats context fallback
    assert (
        _build_asset_title(None, "hero-banner.png", "Frame", "Board", None, "abc123")
        == "hero-banner.png"
    )


def test_build_asset_title_ignores_generic_filename_and_uses_context() -> None:
    # generic filename must not become the title
    assert (
        _build_asset_title(None, "image.png", "Checkout UI", "Board", None, "abc123")
        == "Checkout UI \u2014 abc123"
    )
    # a nearby label provides a distinguishing hint
    assert (
        _build_asset_title(
            None, "image.png", "Checkout UI", "Board", ["Login button"], "abc123"
        )
        == "Checkout UI \u2014 Login button"
    )
    # falls back to board name when no frame
    assert (
        _build_asset_title(None, None, None, "Design Board", None, "abc123")
        == "Design Board \u2014 abc123"
    )


def test_board_deep_link_with_and_without_item() -> None:
    assert _board_deep_link("board1") == "https://miro.com/app/board/board1/"
    assert (
        _board_deep_link("board1", "item1")
        == "https://miro.com/app/board/board1/?moveToWidget=item1"
    )


def test_build_frame_context_maps_titles_and_nearby_text() -> None:
    items = [
        _frame_item("frame1", "Checkout UI"),
        _text_item("text1", "<p>Login button</p>", parent_id="frame1"),
        _text_item("sticky1", "TODO: replace icon", parent_id="frame1", item_type="sticky_note"),
        _text_item("shape1", "Section header", parent_id="frame1", item_type="shape"),
        # Loose text with no parent frame - should not show up anywhere.
        _text_item("text2", "Orphaned label", parent_id=None),
        # Text belonging to a frame with no title - contributes to frame_texts only.
        _text_item("text3", "Untitled frame note", parent_id="frame2"),
        _image_item("img1", parent_id="frame1"),
    ]

    frame_titles, frame_texts = MiroConnector._build_frame_context(items)

    assert frame_titles == {"frame1": "Checkout UI"}
    assert frame_texts["frame1"] == [
        "Login button",
        "TODO: replace icon",
        "Section header",
    ]
    assert frame_texts["frame2"] == ["Untitled frame note"]
    assert "text2" not in frame_texts


def test_build_frame_context_ignores_empty_text_content() -> None:
    items = [
        _text_item("text1", "", parent_id="frame1"),
        _text_item("text2", "<p></p>", parent_id="frame1"),
    ]

    frame_titles, frame_texts = MiroConnector._build_frame_context(items)

    assert frame_titles == {}
    assert frame_texts == {}


class TestItemToDocument:
    def setup_method(self) -> None:
        self.connector = MiroConnector()
        self.connector.miro_access_token = "test-token"

    def test_captionable_image_produces_image_and_context_sections(self) -> None:
        item = _image_item(
            "item1", title="hero.png", parent_id="frame1", modified_at="2024-01-15T12:00:00Z"
        )
        frame_titles = {"frame1": "Checkout UI"}
        frame_texts = {"frame1": ["Login button"]}

        with patch.object(
            self.connector,
            "_fetch_and_classify_image",
            return_value=(b"fake-bytes", "image/png", "hero-abc123.png"),
        ), patch(
            "onyx.connectors.miro.connector.store_image_and_create_section"
        ) as mock_store:
            mock_store.return_value = (
                ImageSection(image_file_id="file-store-id-1", link="deep-link"),
                "file-store-id-1",
            )

            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles=frame_titles,
                frame_texts=frame_texts,
            )

        assert document is not None
        assert document.id == "miro__board1__item1"
        assert document.source == DocumentSource.MIRO
        # asset_title ("hero.png") wins over the recovered filename.
        assert document.semantic_identifier == "hero.png"
        # Thumbnail-auth trick: file_id must equal the stored image's file id.
        assert document.file_id == "file-store-id-1"
        assert document.doc_updated_at == datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        assert len(document.sections) == 2
        image_section, text_section = document.sections
        assert isinstance(image_section, ImageSection)
        assert image_section.image_file_id == "file-store-id-1"
        assert isinstance(text_section, TextSection)
        assert "Board: Design Board" in text_section.text
        assert "Frame: Checkout UI" in text_section.text
        assert "Nearby labels: Login button" in text_section.text
        assert "Asset: hero.png" in text_section.text

        # The same board/frame/label context also rides on the image
        # section's heading, so process_image_sections can fold it into the
        # caption chunk itself (see indexing_pipeline.py::_fold_heading_into_text) -
        # keeping the primary chunk informative even when captioning is weak,
        # errors, or is disabled.
        assert image_section.heading == text_section.text
        assert "Board: Design Board" in (image_section.heading or "")
        assert "Frame: Checkout UI" in (image_section.heading or "")
        assert "Nearby labels: Login button" in (image_section.heading or "")

        assert document.metadata["board_name"] == "Design Board"
        assert document.metadata["frame_title"] == "Checkout UI"
        # Identifiers land in Document.metadata (searchable), not just
        # doc_metadata.hierarchy, so they can back an exact-match Tag filter.
        assert document.metadata["miro_item_id"] == "item1"
        assert document.metadata["board_id"] == "board1"
        assert document.metadata["asset_filename"] == "hero-abc123.png"
        assert document.doc_metadata is not None
        assert document.doc_metadata["hierarchy"]["source_path"] == [
            "Design Board",
            "Checkout UI",
        ]
        assert document.doc_metadata["hierarchy"]["frame_id"] == "frame1"

        mock_store.assert_called_once()
        _, kwargs = mock_store.call_args
        assert kwargs["media_type"] == "image/png"
        assert kwargs["display_name"] == "Design Board / Checkout UI / hero.png"

    def test_generic_filename_not_used_as_title_but_stored_for_exact_lookup(
        self,
    ) -> None:
        """A placeholder filename like image.png must not become the title or
        appear in the embedded context blurb, but IS stored in metadata so
        users can find the asset via exact filename lookup. The captionable doc
        opts into image-derived titles."""
        item = _image_item("item4longenoughid", parent_id="frame1")

        with patch.object(
            self.connector,
            "_fetch_and_classify_image",
            return_value=(b"fake-bytes", "image/png", "image.png"),
        ), patch(
            "onyx.connectors.miro.connector.store_image_and_create_section"
        ) as mock_store:
            mock_store.return_value = (
                ImageSection(image_file_id="file-store-id-9", link="deep-link"),
                "file-store-id-9",
            )
            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles={"frame1": "Checkout UI"},
                frame_texts={"frame1": ["Login button"]},
            )

        assert document is not None
        # Title uses the nearby-label context hint, NOT the generic filename.
        assert document.semantic_identifier == "Checkout UI \u2014 Login button"
        assert document.title == "Checkout UI \u2014 Login button"
        # Generic filename IS stored as an exact-match Tag (filterable, not embedded).
        assert document.metadata["asset_filename"] == "image.png"
        # But the filename must NOT appear in the embedded content blurb.
        image_section, text_section = document.sections
        assert isinstance(text_section, TextSection)
        assert "image.png" not in text_section.text
        assert isinstance(image_section, ImageSection)
        assert "image.png" not in (image_section.heading or "")
        # Captionable image opts into pipeline title derivation.
        assert document.derive_title_from_image is True

    def test_non_captionable_image_does_not_opt_into_title_derivation(self) -> None:
        item = _image_item("item2", title="icon.svg")
        with patch.object(
            self.connector, "_fetch_and_classify_image", return_value=None
        ):
            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles={},
                frame_texts={},
            )
        assert document is not None
        assert document.derive_title_from_image is False

    def test_non_captionable_image_falls_back_to_text_only_document(self) -> None:
        item = _image_item("item2", title="icon.svg")

        with patch.object(
            self.connector, "_fetch_and_classify_image", return_value=None
        ), patch(
            "onyx.connectors.miro.connector.store_image_and_create_section"
        ) as mock_store:
            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles={},
                frame_texts={},
            )

        mock_store.assert_not_called()
        assert document is not None
        assert document.file_id is None
        assert len(document.sections) == 1
        assert isinstance(document.sections[0], TextSection)
        assert "Board: Design Board" in document.sections[0].text

    def test_item_without_image_url_returns_none(self) -> None:
        item = {"id": "item3", "type": "image", "data": {}}

        document = self.connector._item_to_document(
            item=item,
            board_id="board1",
            board_name="Design Board",
            frame_titles={},
            frame_texts={},
        )

        assert document is None

    def test_semantic_identifier_falls_back_to_filename_when_title_empty(
        self,
    ) -> None:
        item = _image_item("item4longenoughid", parent_id="frame1")

        with patch.object(
            self.connector,
            "_fetch_and_classify_image",
            return_value=(b"fake-bytes", "image/png", "recovered-name.png"),
        ), patch(
            "onyx.connectors.miro.connector.store_image_and_create_section"
        ) as mock_store:
            mock_store.return_value = (
                ImageSection(image_file_id="file-store-id-2", link="deep-link"),
                "file-store-id-2",
            )
            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles={"frame1": "Checkout UI"},
                frame_texts={},
            )

        assert document is not None
        assert document.semantic_identifier == "recovered-name.png"
        assert document.metadata["asset_filename"] == "recovered-name.png"

    def test_semantic_identifier_falls_back_to_frame_and_item_ref_when_no_title_or_filename(
        self,
    ) -> None:
        item = _image_item("item4longenoughid", parent_id="frame1")

        with patch.object(
            self.connector, "_fetch_and_classify_image", return_value=None
        ):
            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles={"frame1": "Checkout UI"},
                frame_texts={},
            )

        assert document is not None
        # No title, no recoverable filename: falls back to
        # "<frame_title> — <last 6 chars of item id>", not the bare frame
        # title, so distinct assets in the same frame don't collapse.
        assert document.semantic_identifier == "Checkout UI \u2014 oughid"

    def test_semantic_identifier_falls_back_to_board_when_no_frame(self) -> None:
        item = _image_item("item4longenoughid")

        with patch.object(
            self.connector, "_fetch_and_classify_image", return_value=None
        ):
            document = self.connector._item_to_document(
                item=item,
                board_id="board1",
                board_name="Design Board",
                frame_titles={},
                frame_texts={},
            )

        assert document is not None
        assert document.semantic_identifier == "Design Board \u2014 oughid"

    def test_two_assets_in_same_frame_get_distinct_semantic_identifiers(
        self,
    ) -> None:
        """Regression test for the "every asset in a frame collapses to the
        frame title" bug: with no asset title/filename, each asset must still
        get a unique semantic_identifier."""
        frame_titles = {"frame1": "Research"}

        with patch.object(
            self.connector, "_fetch_and_classify_image", return_value=None
        ):
            doc_a = self.connector._item_to_document(
                item=_image_item("itemAAAAAA", parent_id="frame1"),
                board_id="board1",
                board_name="Design Board",
                frame_titles=frame_titles,
                frame_texts={},
            )
            doc_b = self.connector._item_to_document(
                item=_image_item("itemBBBBBB", parent_id="frame1"),
                board_id="board1",
                board_name="Design Board",
                frame_titles=frame_titles,
                frame_texts={},
            )

        assert doc_a is not None and doc_b is not None
        assert doc_a.semantic_identifier != doc_b.semantic_identifier
        assert doc_a.semantic_identifier == "Research \u2014 AAAAAA"
        assert doc_b.semantic_identifier == "Research \u2014 BBBBBB"


class TestItemInTimeWindow:
    def setup_method(self) -> None:
        self.connector = MiroConnector()

    def test_no_window_always_true(self) -> None:
        item: dict[str, Any] = {"modifiedAt": None}
        assert self.connector._item_in_time_window(item, None, None) is True

    def test_missing_modified_at_excluded_when_window_set(self) -> None:
        item: dict[str, Any] = {}
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert self.connector._item_in_time_window(item, start, None) is False

    def test_within_window(self) -> None:
        item = {"modifiedAt": "2024-01-15T12:00:00Z"}
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 2, 1, tzinfo=timezone.utc)
        assert self.connector._item_in_time_window(item, start, end) is True

    def test_outside_window(self) -> None:
        item = {"modifiedAt": "2023-12-01T12:00:00Z"}
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert self.connector._item_in_time_window(item, start, None) is False


class TestProcessBoardRouting:
    def setup_method(self) -> None:
        self.connector = MiroConnector()
        self.connector.miro_access_token = "test-token"
        self.connector.batch_size = 50

    def test_only_image_items_become_documents(self) -> None:
        board = {"id": "board1", "name": "Design Board"}
        items = [
            _frame_item("frame1", "Checkout UI"),
            _text_item("text1", "Nearby label", parent_id="frame1"),
            _image_item("img1", title="a.png", parent_id="frame1"),
            _image_item("img2", title="b.png"),
            # Item types not handled in the MVP (cards, docs, embeds) are skipped.
            {"id": "card1", "type": "app_card", "data": {}},
        ]

        # _process_board's final (sub-batch-size) batch is only surfaced via the
        # generator's return value, which `yield from` in _process_boards
        # captures - so drive the test through _process_boards, not
        # _process_board directly.
        with patch.object(
            self.connector, "_iter_boards", return_value=iter([board])
        ), patch.object(
            self.connector, "_iter_board_items", return_value=iter(items)
        ), patch.object(
            self.connector, "_fetch_and_classify_image", return_value=None
        ):
            batches = list(self.connector._process_boards())

        docs = [doc for batch in batches for doc in batch]
        assert {doc.id for doc in docs} == {
            "miro__board1__img1",
            "miro__board1__img2",
        }

    def test_conversion_failure_is_logged_and_skipped(self) -> None:
        board = {"id": "board1", "name": "Design Board"}
        items = [_image_item("img1", title="a.png")]

        with patch.object(
            self.connector, "_iter_boards", return_value=iter([board])
        ), patch.object(
            self.connector, "_iter_board_items", return_value=iter(items)
        ), patch.object(
            self.connector,
            "_item_to_document",
            side_effect=RuntimeError("boom"),
        ):
            batches = list(self.connector._process_boards())

        docs = [doc for batch in batches for doc in batch]
        assert docs == []
