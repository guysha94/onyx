"""Miro connector (FORK: miro).

Indexes visual asset boards (images, screenshots, UI mockups, icons) from Miro.
Unlike file-centric connectors, a Miro item is not a file: it's a positioned
widget on a board, optionally grouped inside a frame. This connector creates
one Onyx `Document` per image asset, carrying board/frame context (but no
coordinates/bounding boxes - Onyx has no field for those) so assets are
findable via the vision-LLM caption plus board/frame context words.

See `README.md` in this directory for the auth setup and design rationale.
"""
import html
import os
import re
from collections.abc import Generator
from collections.abc import Iterator
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import cast
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

from onyx.configs.app_configs import INDEX_BATCH_SIZE
from onyx.configs.constants import DocumentSource
from onyx.configs.constants import FileOrigin
from onyx.connectors.cross_connector_utils.miscellaneous_utils import time_str_to_utc
from onyx.connectors.cross_connector_utils.rate_limit_wrapper import rl_requests
from onyx.connectors.exceptions import ConnectorValidationError
from onyx.connectors.exceptions import CredentialExpiredError
from onyx.connectors.exceptions import InsufficientPermissionsError
from onyx.connectors.interfaces import GenerateDocumentsOutput
from onyx.connectors.interfaces import LoadConnector
from onyx.connectors.interfaces import PollConnector
from onyx.connectors.interfaces import SecondsSinceUnixEpoch
from onyx.connectors.models import ConnectorMissingCredentialError
from onyx.connectors.models import Document
from onyx.connectors.models import ImageSection
from onyx.connectors.models import TextSection
from onyx.file_processing.file_types import OnyxMimeTypes
from onyx.file_processing.image_utils import store_image_and_create_section
from onyx.utils.b64 import get_image_type_from_bytes
from onyx.utils.logger import setup_logger

logger = setup_logger()

_NUM_RETRIES = 5
_TIMEOUT = 60
_BOARDS_PAGE_LIMIT = 50
_ITEMS_PAGE_LIMIT = 50
_MIRO_API_BASE = "https://api.miro.com/v2"

# Item types that carry short free-text content. These are never indexed as
# their own documents in the MVP; their text is only used as frame-level
# context for the image assets that share their frame.
_LABEL_ITEM_TYPES = {"text", "sticky_note", "shape"}

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw_html: str | None) -> str:
    """Miro returns text/sticky_note/shape content as HTML fragments."""
    if not raw_html:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return " ".join(text.split())


def _board_deep_link(board_id: str, item_id: str | None = None) -> str:
    if item_id:
        return f"https://miro.com/app/board/{board_id}/?moveToWidget={item_id}"
    return f"https://miro.com/app/board/{board_id}/"


class MiroConnector(LoadConnector, PollConnector):
    def __init__(
        self,
        board_ids: list[str] | None = None,
        team_id: str | None = None,
        batch_size: int = INDEX_BATCH_SIZE,
    ) -> None:
        self.board_ids = board_ids or None
        self.team_id = team_id
        self.batch_size = batch_size
        self.miro_access_token: str | None = None

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        if "miro_access_token" not in credentials:
            raise ConnectorMissingCredentialError("Miro")

        self.miro_access_token = cast(str, credentials["miro_access_token"])
        return None

    def _headers(self) -> dict[str, str]:
        if self.miro_access_token is None:
            raise ConnectorMissingCredentialError("Miro")

        return {
            "Authorization": f"Bearer {self.miro_access_token}",
            "Accept": "application/json",
        }

    def _get_json(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.miro_access_token is None:
            raise ConnectorMissingCredentialError("Miro")

        for attempt in range(_NUM_RETRIES):
            try:
                response = rl_requests.get(
                    url, headers=self._headers(), params=params, timeout=_TIMEOUT
                )
                if response.status_code == 401:
                    raise CredentialExpiredError(
                        "Invalid Miro access token (HTTP 401)."
                    )
                if response.status_code == 403:
                    raise InsufficientPermissionsError(
                        "Insufficient permissions for the Miro API (HTTP 403)."
                    )
                if not response.ok:
                    raise RuntimeError(
                        f"Error calling Miro API {url} "
                        f"(status={response.status_code}): {response.text}"
                    )
                return cast(dict[str, Any], response.json())
            except (CredentialExpiredError, InsufficientPermissionsError):
                raise
            except Exception as exc:
                if attempt == _NUM_RETRIES - 1:
                    raise exc
                logger.warning(
                    "A Miro API error occurred calling %s: %s. Retrying...", url, exc
                )

        raise RuntimeError(
            "Unexpected execution when calling the Miro API. This should never happen."
        )

    def validate_connector_settings(self) -> None:
        if self.miro_access_token is None:
            raise ConnectorMissingCredentialError("Miro")

        try:
            self._get_json(f"{_MIRO_API_BASE}/boards", params={"limit": 1})
        except (CredentialExpiredError, InsufficientPermissionsError):
            raise
        except Exception as exc:
            raise ConnectorValidationError(
                f"Unexpected error while validating Miro connector settings: {exc}"
            ) from exc

    def _iter_boards(self) -> Iterator[dict[str, Any]]:
        if self.board_ids:
            for board_id in self.board_ids:
                try:
                    yield self._get_json(f"{_MIRO_API_BASE}/boards/{board_id}")
                except Exception:
                    logger.exception("Failed to fetch Miro board %s", board_id)
            return

        offset = 0
        while True:
            params: dict[str, Any] = {"limit": _BOARDS_PAGE_LIMIT, "offset": offset}
            if self.team_id:
                params["team_id"] = self.team_id

            response = self._get_json(f"{_MIRO_API_BASE}/boards", params=params)
            boards = response.get("data") or []
            yield from boards

            total = response.get("total", 0)
            offset += _BOARDS_PAGE_LIMIT
            if not boards or offset >= total:
                break

    def _iter_board_items(self, board_id: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": _ITEMS_PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor

            response = self._get_json(
                f"{_MIRO_API_BASE}/boards/{board_id}/items", params=params
            )
            items = response.get("data") or []
            yield from items

            cursor = response.get("cursor")
            if not cursor or not items:
                break

    def _download_image_bytes(self, image_url: str) -> bytes:
        # Force the original (non-preview) image and a direct redirect to the
        # binary, overriding whatever `format`/`redirect` the API returned.
        parsed = urlparse(image_url)
        query = parse_qs(parsed.query)
        query["format"] = ["original"]
        query["redirect"] = ["true"]
        download_url = urlunparse(
            parsed._replace(query=urlencode(query, doseq=True))
        )

        response = rl_requests.get(
            download_url, headers=self._headers(), timeout=_TIMEOUT
        )
        if not response.ok:
            raise RuntimeError(
                f"Failed to download Miro image (status={response.status_code}): "
                f"{download_url}"
            )
        return response.content

    def _fetch_and_classify_image(self, image_url: str) -> tuple[bytes, str] | None:
        """Downloads the original image bytes and returns `(bytes, mime)` if it's
        a captionable type (png/jpg/webp). Returns `None` for undetectable or
        excluded types (svg, gif, ...) or on download failure, so the caller can
        fall back to a text-only document.
        """
        try:
            image_bytes = self._download_image_bytes(image_url)
        except Exception:
            logger.warning(
                "Failed to download Miro image from %s", image_url, exc_info=True
            )
            return None

        try:
            mime = get_image_type_from_bytes(image_bytes)
        except ValueError:
            return None

        if mime not in OnyxMimeTypes.IMAGE_MIME_TYPES:
            return None

        return image_bytes, mime

    @staticmethod
    def _build_frame_context(
        items: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Builds `frame_id -> frame title` and `frame_id -> nearby text/sticky/
        shape content in that frame` maps from a board's flat item list.

        No coordinate math: membership is determined purely by each item's
        `parent.id` (the Miro API's own frame-containment field), since these
        boards are dense unlabeled asset grids where x/y proximity would be
        noisy and unreliable.
        """
        frame_titles: dict[str, str] = {}
        frame_texts: dict[str, list[str]] = {}

        for item in items:
            item_id = str(item.get("id"))
            item_type = item.get("type")
            data = item.get("data") or {}

            if item_type == "frame":
                title = data.get("title")
                if title:
                    frame_titles[item_id] = title
                continue

            if item_type in _LABEL_ITEM_TYPES:
                parent_id = (item.get("parent") or {}).get("id")
                if not parent_id:
                    continue
                text = _strip_html(data.get("content"))
                if text:
                    frame_texts.setdefault(str(parent_id), []).append(text)

        return frame_titles, frame_texts

    def _item_to_document(
        self,
        item: dict[str, Any],
        board_id: str,
        board_name: str,
        frame_titles: dict[str, str],
        frame_texts: dict[str, list[str]],
    ) -> Document | None:
        item_id = str(item["id"])
        data = item.get("data") or {}
        image_url = data.get("imageUrl")
        if not image_url:
            return None

        asset_title = data.get("title") or None
        frame_id = (item.get("parent") or {}).get("id")
        frame_title = frame_titles.get(str(frame_id)) if frame_id else None
        nearby_labels = frame_texts.get(str(frame_id)) if frame_id else None

        semantic_identifier = asset_title or frame_title or f"{board_name} \u2014 asset"
        deep_link = _board_deep_link(board_id, item_id)

        # Board/frame context - the only "structure" signal we attach, since
        # Onyx has no field for coordinates/bounding boxes. This is what the
        # vision LLM caption and the embedded text section pick up.
        context_lines = [f"Board: {board_name}"]
        if frame_title:
            context_lines.append(f"Frame: {frame_title}")
        if nearby_labels:
            context_lines.append("Nearby labels: " + "; ".join(nearby_labels))
        context_blurb = "\n".join(context_lines)

        display_name_parts = [board_name]
        if frame_title:
            display_name_parts.append(frame_title)
        if asset_title:
            display_name_parts.append(asset_title)
        display_name = " / ".join(display_name_parts)

        image_result = self._fetch_and_classify_image(image_url)

        sections: list[TextSection | ImageSection]
        image_file_id: str | None = None
        doc_id = f"miro__{board_id}__{item_id}"

        if image_result is not None:
            image_bytes, media_type = image_result
            image_section, image_file_id = store_image_and_create_section(
                image_data=image_bytes,
                file_id=doc_id,
                display_name=display_name,
                link=deep_link,
                media_type=media_type,
                file_origin=FileOrigin.CONNECTOR,
            )
            sections = [image_section, TextSection(text=context_blurb, link=deep_link)]
        else:
            # Non-captionable image type (svg/gif/...): no binary is stored,
            # just a text-only document carrying the same context.
            sections = [
                TextSection(text=f"{display_name}\n\n{context_blurb}", link=deep_link)
            ]

        metadata: dict[str, str] = {"board_name": board_name, "item_type": "image"}
        if frame_title:
            metadata["frame_title"] = frame_title

        source_path = [board_name] + ([frame_title] if frame_title else [])

        return Document(
            id=doc_id,
            sections=cast(list[TextSection | ImageSection], sections),
            source=DocumentSource.MIRO,
            semantic_identifier=semantic_identifier,
            title=semantic_identifier,
            doc_updated_at=(
                time_str_to_utc(item["modifiedAt"]) if item.get("modifiedAt") else None
            ),
            doc_metadata={
                "hierarchy": {
                    "source_path": source_path,
                    "board_id": board_id,
                    "frame_id": str(frame_id) if frame_id else None,
                }
            },
            metadata=metadata,
            # Thumbnail-auth trick: setting Document.file_id equal to the
            # stored image's file-store id lets the existing
            # `/api/chat/file/{file_id}` ACL check (which grants access when
            # `Document.file_id == file_id`) serve the image with no backend
            # auth changes. See ee/onyx/server/query_and_chat/models.py and
            # web result cards for the other half of this.
            file_id=image_file_id,
        )

    def _item_in_time_window(
        self,
        item: dict[str, Any],
        start: datetime | None,
        end: datetime | None,
    ) -> bool:
        if start is None and end is None:
            return True

        modified_at = item.get("modifiedAt")
        if not modified_at:
            return False

        updated = time_str_to_utc(modified_at)
        if start is not None and updated < start:
            return False
        if end is not None and updated > end:
            return False
        return True

    def _process_board(
        self,
        board: dict[str, Any],
        start: datetime | None,
        end: datetime | None,
        batch: list[Document],
    ) -> Generator[list[Document], None, list[Document]]:
        board_id = str(board["id"])
        board_name = board.get("name") or f"Board {board_id}"

        items = list(self._iter_board_items(board_id))
        frame_titles, frame_texts = self._build_frame_context(items)

        for item in items:
            if item.get("type") != "image":
                continue
            if not self._item_in_time_window(item, start, end):
                continue

            try:
                document = self._item_to_document(
                    item=item,
                    board_id=board_id,
                    board_name=board_name,
                    frame_titles=frame_titles,
                    frame_texts=frame_texts,
                )
            except Exception:
                # LoadConnector/PollConnector's GenerateDocumentsOutput can't
                # carry per-item failures (that's a CheckpointedConnector-only
                # feature), so we log and skip rather than aborting the run.
                logger.exception(
                    "Failed to convert Miro item %s on board %s to a document",
                    item.get("id"),
                    board_id,
                )
                continue

            if document is None:
                continue

            batch.append(document)
            if len(batch) >= self.batch_size:
                yield batch
                batch = []

        return batch

    def _process_boards(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> GenerateDocumentsOutput:
        if self.miro_access_token is None:
            raise ConnectorMissingCredentialError("Miro")

        batch: list[Document] = []
        for board in self._iter_boards():
            batch = yield from self._process_board(board, start, end, batch)

        if batch:
            yield batch

    def load_from_state(self) -> GenerateDocumentsOutput:
        yield from self._process_boards()

    def poll_source(
        self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch
    ) -> GenerateDocumentsOutput:
        start_time = datetime.fromtimestamp(start, tz=timezone.utc)
        end_time = datetime.fromtimestamp(end, tz=timezone.utc)
        yield from self._process_boards(start=start_time, end=end_time)


if __name__ == "__main__":
    connector = MiroConnector()
    connector.load_credentials({"miro_access_token": os.environ["MIRO_ACCESS_TOKEN"]})
    connector.validate_connector_settings()
    print(next(connector.load_from_state()))
