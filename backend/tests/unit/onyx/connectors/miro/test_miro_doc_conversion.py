from datetime import datetime
from datetime import timezone
from typing import Any
from unittest.mock import patch

from onyx.configs.constants import DocumentSource
from onyx.connectors.miro.connector import MiroConnector
from onyx.connectors.miro.connector import _board_deep_link
from onyx.connectors.miro.connector import _strip_html
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
            return_value=(b"fake-bytes", "image/png"),
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

        assert document.metadata["board_name"] == "Design Board"
        assert document.metadata["frame_title"] == "Checkout UI"
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

    def test_semantic_identifier_falls_back_to_frame_then_board(self) -> None:
        item = _image_item("item4", parent_id="frame1")

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
        assert document.semantic_identifier == "Checkout UI"


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
