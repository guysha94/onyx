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
from urllib.parse import unquote
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
_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE
)


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


def _filename_from_content_disposition(header_value: str | None) -> str | None:
    if not header_value:
        return None
    match = _CONTENT_DISPOSITION_FILENAME_RE.search(header_value)
    if not match:
        return None
    filename = unquote(match.group(1).strip())
    return filename or None


def _filename_from_url(url: str) -> str | None:
    basename = os.path.basename(urlparse(url).path)
    return basename or None


# Generic, non-descriptive filename stems that make useless titles (e.g.
# "image.png", "download-1.jpg", "screenshot.png", "untitled.png"). We never use
# these as the document title; a board/frame context title is used instead.
_GENERIC_FILENAME_STEM_RE = re.compile(
    r"^(image|img|download|untitled|screenshot|screen[\s_-]?shot|photo|"
    r"unnamed|copy|paste[d]?|frame|asset|picture|pic)"
    r"[\s_\-()0-9]*$",
    re.IGNORECASE,
)

# Canonical UUID (with or without dashes) and long bare hex/token stems - these
# are machine-generated identifiers (e.g. "7ff78985-de21-439d-bcc6-b9902edec5d0",
# "95d9ddb8737b52763daa1b10c7c6c665"), never human-meaningful titles.
_UUID_STEM_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LONG_HEX_STEM_RE = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)


def _is_meaningful_filename(filename: str | None) -> bool:
    """A filename is meaningful (usable as a title) only if its stem is not a
    generic placeholder like ``image.png`` / ``download-2.jpg`` / ``screenshot``,
    not a machine-generated id (UUID / long hex hash like ``7ff78985-...png`` or
    ``95d9ddb8...jpg``), and is not a bare number or too short to carry meaning
    (e.g. ``4.png``, ``12.jpg``, ``a.png`` are auto-generated/placeholder-style
    stems, not real asset names)."""
    if not filename:
        return False
    stem = os.path.splitext(filename)[0].strip()
    if not stem:
        return False
    if len(stem) <= 2 or stem.isdigit():
        return False
    if _UUID_STEM_RE.match(stem) or _LONG_HEX_STEM_RE.match(stem):
        return False
    return not _GENERIC_FILENAME_STEM_RE.match(stem)


def _build_asset_title(
    asset_title: str | None,
    asset_filename: str | None,
    frame_title: str | None,
    board_name: str,
    nearby_labels: list[str] | None,
    short_item_ref: str,
) -> str:
    """Deterministic, meaningful title for a Miro image asset. Never a bare
    placeholder filename like ``image.png``. This is the fallback the indexing
    pipeline keeps when vision captioning (which produces a nicer image-derived
    title) is unavailable.
    """
    if asset_title:
        return asset_title
    if _is_meaningful_filename(asset_filename):
        return cast(str, asset_filename)

    location = frame_title or board_name
    # A short hint from nearby labels keeps sibling assets in the same frame
    # distinguishable when there's nothing else to go on.
    if nearby_labels:
        hint = nearby_labels[0].strip()
        if len(hint) > 40:
            hint = hint[:39].rstrip() + "\u2026"
        if hint:
            return f"{location} \u2014 {hint}"
    # No title, filename, or labels: keep the frame/board + short item ref so
    # distinct assets in the same frame don't collapse to an identical title.
    return f"{location} \u2014 {short_item_ref}"


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

    def _download_image_bytes(self, image_url: str) -> tuple[bytes, str | None]:
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

        # The `imageUrl` the API returns is an opaque API URL, not the
        # asset's real filename. The real filename (what a user would paste
        # to search for the asset) is only recoverable here, from either the
        # Content-Disposition header or the redirected response.url basename.
        filename = _filename_from_content_disposition(
            response.headers.get("Content-Disposition")
        ) or _filename_from_url(response.url)

        return response.content, filename

    def _fetch_and_classify_image(
        self, image_url: str
    ) -> tuple[bytes, str, str | None] | None:
        """Downloads the original image bytes and returns
        `(bytes, mime, filename)` if it's a captionable type (png/jpg/webp).
        Returns `None` for undetectable or excluded types (svg, gif, ...) or
        on download failure, so the caller can fall back to a text-only
        document.
        """
        try:
            image_bytes, filename = self._download_image_bytes(image_url)
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

        return image_bytes, mime, filename

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
        deep_link = _board_deep_link(board_id, item_id)

        image_result = self._fetch_and_classify_image(image_url)
        asset_filename = image_result[2] if image_result else None

        # Short, unique-enough fragment of the item id to disambiguate assets
        # that have neither a title nor a recoverable filename (e.g. when
        # download failed) - keeps every asset in a frame from collapsing to
        # an identical semantic_identifier.
        short_item_ref = item_id[-6:] if len(item_id) >= 6 else item_id

        # Deterministic, meaningful title (never a placeholder filename like
        # "image.png"). When vision captioning succeeds, the indexing pipeline
        # replaces this with an image-derived title (see derive_title_from_image);
        # this remains the fallback.
        semantic_identifier = _build_asset_title(
            asset_title=asset_title,
            asset_filename=asset_filename,
            frame_title=frame_title,
            board_name=board_name,
            nearby_labels=nearby_labels,
            short_item_ref=short_item_ref,
        )

        # Board/frame context - the only "structure" signal we attach, since
        # Onyx has no field for coordinates/bounding boxes. This is what the
        # vision LLM caption and the embedded text section pick up. The
        # per-asset line at the end keeps this text distinct across assets in
        # the same frame, so results are distinguishable even when this text
        # chunk (rather than the image's vision caption) is the top hit.
        # It is also folded into the image section's own chunk (see the
        # `heading` assignment below / process_image_sections in
        # indexing_pipeline.py) so the caption chunk itself is never thin,
        # even when the vision caption is weak, missing, or disabled.
        meaningful_filename = (
            asset_filename if _is_meaningful_filename(asset_filename) else None
        )
        context_lines = [f"Board: {board_name}"]
        if frame_title:
            context_lines.append(f"Frame: {frame_title}")
        if nearby_labels:
            context_lines.append("Nearby labels: " + "; ".join(nearby_labels))
        context_lines.append(
            f"Asset: {asset_title or meaningful_filename or semantic_identifier}"
        )
        context_blurb = "\n".join(context_lines)

        display_name_parts = [board_name]
        if frame_title:
            display_name_parts.append(frame_title)
        if asset_title:
            display_name_parts.append(asset_title)
        display_name = " / ".join(display_name_parts)

        sections: list[TextSection | ImageSection]
        image_file_id: str | None = None
        doc_id = f"miro__{board_id}__{item_id}"

        if image_result is not None:
            image_bytes, media_type, _ = image_result
            image_section, image_file_id = store_image_and_create_section(
                image_data=image_bytes,
                file_id=doc_id,
                display_name=display_name,
                link=deep_link,
                media_type=media_type,
                file_origin=FileOrigin.CONNECTOR,
            )
            # FORK: miro - `heading` rides along with the image section and is
            # folded into its text by process_image_sections once the vision
            # caption is generated (or used as-is if captioning fails/is
            # disabled), making the caption chunk itself informative. The
            # separate TextSection below is kept as a content-hash-stable,
            # LLM-independent copy of the same context (content_hash() only
            # covers TextSection text, not Section.heading - see
            # Document.content_hash), so renamed frames/boards are still
            # detected as changes even when the item's own modifiedAt doesn't
            # advance.
            image_section.heading = context_blurb
            sections = [image_section, TextSection(text=context_blurb, link=deep_link)]
        else:
            # Non-captionable image type (svg/gif/...): no binary is stored,
            # just a text-only document carrying the same context.
            sections = [
                TextSection(text=f"{display_name}\n\n{context_blurb}", link=deep_link)
            ]

        # asset_filename/miro_item_id/board_id land in Document.metadata (not
        # doc_metadata.hierarchy) so they flow into the searchable metadata
        # suffix and can be used as an exact-match Tag filter - see
        # ee/onyx/search/process_search_query.py::_maybe_exact_lookup.
        metadata: dict[str, str] = {
            "board_name": board_name,
            "item_type": "image",
            "miro_item_id": item_id,
            "board_id": board_id,
        }
        if frame_title:
            metadata["frame_title"] = frame_title
        # Always store the raw filename so users can find assets by filename
        # even when it's a generic name like "image_720.png". Only meaningful
        # filenames are used as titles; all filenames remain searchable.
        if asset_filename:
            metadata["asset_filename"] = asset_filename

        source_path = [board_name] + ([frame_title] if frame_title else [])

        return Document(
            id=doc_id,
            sections=cast(list[TextSection | ImageSection], sections),
            source=DocumentSource.MIRO,
            semantic_identifier=semantic_identifier,
            title=semantic_identifier,
            # Let the indexing pipeline upgrade the title to an image-derived one
            # when vision captioning succeeds; semantic_identifier is the fallback.
            derive_title_from_image=image_file_id is not None,
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
