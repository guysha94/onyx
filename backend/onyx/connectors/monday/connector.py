import os
from collections.abc import Generator
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import TypedDict
from typing import cast

from onyx.access.models import ExternalAccess
from onyx.configs.app_configs import INDEX_BATCH_SIZE
from onyx.configs.constants import DocumentSource
from onyx.connectors.cross_connector_utils.miscellaneous_utils import time_str_to_utc
from onyx.connectors.exceptions import ConnectorValidationError
from onyx.connectors.exceptions import CredentialExpiredError
from onyx.connectors.exceptions import InsufficientPermissionsError
from onyx.connectors.interfaces import GenerateDocumentsOutput
from onyx.connectors.interfaces import GenerateSlimDocumentOutput
from onyx.connectors.interfaces import LoadConnector
from onyx.connectors.interfaces import PollConnector
from onyx.connectors.interfaces import SecondsSinceUnixEpoch
from onyx.connectors.interfaces import SlimConnector
from onyx.connectors.interfaces import SlimConnectorWithPermSync
from onyx.connectors.models import BasicExpertInfo
from onyx.connectors.models import ConnectorMissingCredentialError
from onyx.connectors.models import Document
from onyx.connectors.models import ImageSection
from onyx.connectors.models import SlimDocument
from onyx.connectors.models import TextSection
from onyx.connectors.monday.access import fetch_board_access_data
from onyx.connectors.monday.access import get_board_permissions
from onyx.connectors.monday.client import MondayApiClient
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.utils.logger import setup_logger

logger = setup_logger()

_BOARDS_PAGE_LIMIT = 50
_ITEMS_PAGE_LIMIT = 500

_ITEM_FIELDS_FRAGMENT = """
fragment ItemFields on Item {
    id
    name
    url
    created_at
    updated_at
    group {
        title
    }
    creator {
        name
        email
    }
    column_values {
        id
        text
        type
        column {
            title
        }
    }
    updates(limit: 50) {
        body
        created_at
        creator {
            name
            email
        }
    }
    assets {
        name
        public_url
        url
    }
}
"""

_LIST_BOARDS_QUERY = """
query MondayListBoards(
    $boardsLimit: Int!
    $page: Int!
    $boardIds: [ID!]
    $workspaceIds: [ID!]
) {
    boards(
        limit: $boardsLimit
        page: $page
        ids: $boardIds
        workspace_ids: $workspaceIds
    ) {
        id
        name
        workspace {
            id
            name
        }
    }
}
"""

_BOARD_ITEMS_PAGE_QUERY = (
    _ITEM_FIELDS_FRAGMENT
    + """
query MondayBoardItemsPage($boardId: ID!, $itemsLimit: Int!) {
    boards(ids: [$boardId]) {
        id
        name
        workspace {
            id
            name
        }
        items_page(limit: $itemsLimit) {
            cursor
            items {
                ...ItemFields
            }
        }
    }
}
"""
)

_NEXT_ITEMS_PAGE_QUERY = (
    _ITEM_FIELDS_FRAGMENT
    + """
query MondayNextItemsPage($cursor: String!, $itemsLimit: Int!) {
    next_items_page(limit: $itemsLimit, cursor: $cursor) {
        cursor
        items {
            ...ItemFields
        }
    }
}
"""
)

_SLIM_ITEM_FIELDS_FRAGMENT = """
fragment SlimItemFields on Item {
    id
    url
    updated_at
    subscribers {
        email
    }
}
"""

_SLIM_BOARD_ITEMS_PAGE_QUERY = (
    _SLIM_ITEM_FIELDS_FRAGMENT
    + """
query MondaySlimBoardItemsPage($boardId: ID!, $itemsLimit: Int!) {
    boards(ids: [$boardId]) {
        id
        items_page(limit: $itemsLimit) {
            cursor
            items {
                ...SlimItemFields
            }
        }
    }
}
"""
)

_SLIM_NEXT_ITEMS_PAGE_QUERY = (
    _SLIM_ITEM_FIELDS_FRAGMENT
    + """
query MondaySlimNextItemsPage($cursor: String!, $itemsLimit: Int!) {
    next_items_page(limit: $itemsLimit, cursor: $cursor) {
        cursor
        items {
            ...SlimItemFields
        }
    }
}
"""
)

_VALIDATE_QUERY = """
query MondayValidate {
    me {
        id
    }
}
"""


class BoardContext(TypedDict):
    board_id: str
    board_name: str
    workspace_id: str
    workspace_name: str


def _normalize_id_filter(ids: list[str] | None) -> list[str] | None:
    if not ids:
        return None
    return ids


def _item_in_time_window(
    updated_at_str: str | None,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    if not updated_at_str:
        return False

    updated_at = time_str_to_utc(updated_at_str)
    if start is not None and updated_at < start:
        return False
    if end is not None and updated_at > end:
        return False
    return True


def _render_column_values(column_values: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for column_value in column_values:
        column_title = (column_value.get("column") or {}).get(
            "title"
        ) or column_value.get("id", "")
        text = column_value.get("text") or ""
        if text:
            lines.append(f"{column_title}: {text}")
    return "\n".join(lines)


def _render_assets(assets: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for asset in assets:
        name = asset.get("name") or "file"
        url = asset.get("public_url") or asset.get("url") or ""
        lines.append(f"{name}: {url}" if url else name)
    return "\n".join(lines)


def _column_metadata(column_values: list[dict[str, Any]]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for column_value in column_values:
        column_title = (column_value.get("column") or {}).get("title")
        if not column_title:
            continue
        text = column_value.get("text")
        if text:
            metadata[column_title] = str(text)
    return metadata


def _is_fallback_workspace_name(workspace_name: str, workspace_id: str) -> bool:
    if not workspace_name or workspace_name == "Workspace":
        return True
    if workspace_id and workspace_name == f"Workspace {workspace_id}":
        return True
    return False


def _board_context_from_board(
    board: dict[str, Any],
    *,
    fallback_workspace_id: str = "",
    fallback_workspace_name: str = "Workspace",
) -> BoardContext:
    board_id = str(board["id"])
    board_name = board.get("name") or f"Board {board_id}"
    workspace = board.get("workspace") or {}
    workspace_id = str(workspace.get("id") or fallback_workspace_id or "")
    workspace_name = workspace.get("name") or ""
    if not workspace_name or _is_fallback_workspace_name(workspace_name, workspace_id):
        if fallback_workspace_name and not _is_fallback_workspace_name(
            fallback_workspace_name, workspace_id or fallback_workspace_id
        ):
            workspace_name = fallback_workspace_name
        elif not workspace_name:
            workspace_name = (
                f"Workspace {workspace_id}" if workspace_id else "Workspace"
            )
    return BoardContext(
        board_id=board_id,
        board_name=board_name,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
    )


def _merge_board_workspace_context(
    board_context: BoardContext,
    board: dict[str, Any],
) -> BoardContext:
    """Prefer workspace id/name from the board payload over list-workspaces fallbacks."""
    workspace = board.get("workspace") or {}
    workspace_id = str(workspace.get("id") or board_context["workspace_id"] or "")
    workspace_name = workspace.get("name") or ""
    if not workspace_name or _is_fallback_workspace_name(workspace_name, workspace_id):
        if not _is_fallback_workspace_name(
            board_context["workspace_name"], board_context["workspace_id"]
        ):
            workspace_name = board_context["workspace_name"]
        elif not workspace_name:
            workspace_name = (
                f"Workspace {workspace_id}" if workspace_id else "Workspace"
            )
    return BoardContext(
        board_id=board_context["board_id"],
        board_name=board.get("name") or board_context["board_name"],
        workspace_id=workspace_id,
        workspace_name=workspace_name,
    )


def _hierarchy_context_blurb(workspace_name: str, board_name: str) -> str:
    return f"Workspace: {workspace_name}\nBoard: {board_name}"


class MondayConnector(
    LoadConnector, PollConnector, SlimConnector, SlimConnectorWithPermSync
):
    def __init__(
        self,
        board_ids: list[str] | None = None,
        workspace_ids: list[str] | None = None,
        batch_size: int = INDEX_BATCH_SIZE,
    ) -> None:
        self.board_ids = board_ids
        self.workspace_ids = workspace_ids
        self.batch_size = batch_size
        self._client: MondayApiClient | None = None
        self._board_access_data_cache: dict[str, dict[str, Any] | None] = {}

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        if "monday_api_token" not in credentials:
            raise ConnectorMissingCredentialError("Monday")

        self._client = MondayApiClient(cast(str, credentials["monday_api_token"]))
        return None

    def _require_client(self) -> MondayApiClient:
        if self._client is None:
            raise ConnectorMissingCredentialError("Monday")
        return self._client

    def _run_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._require_client().run_query(query, variables)

    def _workspace_name_by_id(self, workspace_ids: list[str]) -> dict[str, str]:
        client = self._require_client()
        all_workspaces = client.list_workspaces()
        names = {
            str(workspace["id"]): workspace.get("name")
            or f"Workspace {workspace['id']}"
            for workspace in all_workspaces
        }
        return {
            workspace_id: names.get(workspace_id, f"Workspace {workspace_id}")
            for workspace_id in workspace_ids
        }

    def _iter_board_contexts(self) -> Generator[BoardContext, None, None]:
        """Yield board contexts with workspace names from board GraphQL payloads.

        SDK ``fetch_boards`` omits ``workspace``; closed workspaces may also be
        missing from ``get_workspaces``. Always list boards via ``_LIST_BOARDS_QUERY``
        so ``workspace { id name }`` is available on each board.
        """
        board_ids = _normalize_id_filter(self.board_ids)
        workspace_ids = _normalize_id_filter(self.workspace_ids)

        if board_ids and not workspace_ids:
            page = 1
            while True:
                boards = self._run_query(
                    _LIST_BOARDS_QUERY,
                    {
                        "boardsLimit": _BOARDS_PAGE_LIMIT,
                        "page": page,
                        "boardIds": board_ids,
                        "workspaceIds": None,
                    },
                ).get("boards", [])
                if not boards:
                    break
                for board in boards:
                    yield _board_context_from_board(board)
                # Explicit board IDs are returned in one page by Monday.
                break
            return

        if workspace_ids:
            workspace_names = self._workspace_name_by_id(workspace_ids)
            target_workspace_ids = workspace_ids
        else:
            client = self._require_client()
            listed = client.list_workspaces()
            target_workspace_ids = [str(workspace["id"]) for workspace in listed]
            workspace_names = {
                str(workspace["id"]): workspace.get("name")
                or f"Workspace {workspace['id']}"
                for workspace in listed
            }

        for workspace_id in target_workspace_ids:
            page = 1
            while True:
                boards = self._run_query(
                    _LIST_BOARDS_QUERY,
                    {
                        "boardsLimit": _BOARDS_PAGE_LIMIT,
                        "page": page,
                        "boardIds": board_ids,
                        "workspaceIds": [workspace_id],
                    },
                ).get("boards", [])
                if not boards:
                    break

                for board in boards:
                    yield _board_context_from_board(
                        board,
                        fallback_workspace_id=workspace_id,
                        fallback_workspace_name=workspace_names.get(
                            workspace_id, f"Workspace {workspace_id}"
                        ),
                    )

                if board_ids or len(boards) < _BOARDS_PAGE_LIMIT:
                    break
                page += 1

    def validate_connector_settings(self) -> None:
        try:
            data = self._run_query(_VALIDATE_QUERY)
            if not data.get("me", {}).get("id"):
                raise ConnectorValidationError(
                    "monday.com validation query did not return a user id."
                )
        except (CredentialExpiredError, InsufficientPermissionsError):
            raise
        except ConnectorMissingCredentialError:
            raise
        except Exception as exc:
            raise ConnectorValidationError(
                f"Unexpected error while validating monday.com connector settings: {exc}"
            ) from exc

    def validate_perm_sync(self) -> None:
        board_contexts = list(self._iter_board_contexts())
        if not board_contexts:
            raise ConnectorValidationError(
                "monday.com permission sync validation could not find any accessible boards."
            )

        board_id = board_contexts[0]["board_id"]
        external_access = get_board_permissions(
            self._run_query, board_id, add_prefix=False
        )
        if external_access is None:
            raise ConnectorValidationError(
                "monday.com permission sync requires Enterprise Edition."
            )
        if not external_access.is_public and not external_access.external_user_emails:
            raise ConnectorValidationError(
                f"monday.com permission sync resolved an empty private ACL for "
                f"board {board_id}. Ensure the API token includes scopes: "
                "boards:read, workspaces:read, users:read, teams:read."
            )

    def _get_board_access_data(self, board_id: str) -> dict[str, Any] | None:
        if board_id not in self._board_access_data_cache:
            self._board_access_data_cache[board_id] = fetch_board_access_data(
                self._run_query, board_id
            )

        return self._board_access_data_cache[board_id]

    def _get_item_external_access(
        self,
        board_id: str,
        item: dict[str, Any],
        *,
        add_prefix: bool,
    ) -> ExternalAccess | None:
        return get_board_permissions(
            self._run_query,
            board_id,
            item=item,
            add_prefix=add_prefix,
            board_data=self._get_board_access_data(board_id),
        )

    def _build_document(
        self,
        item: dict[str, Any],
        board_context: BoardContext,
    ) -> Document:
        item_id = str(item["id"])
        item_name = item.get("name") or f"Item {item_id}"
        item_url = item.get("url") or f"monday__{item_id}"

        board_id = board_context["board_id"]
        board_name = board_context["board_name"]
        workspace_id = board_context["workspace_id"]
        workspace_name = board_context["workspace_name"]

        column_values = item.get("column_values") or []
        assets = item.get("assets") or []
        group_title = (item.get("group") or {}).get("title")

        context_blurb = _hierarchy_context_blurb(workspace_name, board_name)
        body_parts = [context_blurb, item_name]
        column_text = _render_column_values(column_values)
        if column_text:
            body_parts.append(column_text)
        asset_text = _render_assets(assets)
        if asset_text:
            body_parts.append(asset_text)

        sections: list[TextSection | ImageSection] = [
            TextSection(link=item_url, text="\n\n".join(body_parts))
        ]

        for update in item.get("updates") or []:
            update_body = update.get("body") or ""
            if update_body:
                sections.append(TextSection(link=item_url, text=update_body))

        creator = item.get("creator") or {}
        primary_owners: list[BasicExpertInfo] | None = None
        if creator.get("name") or creator.get("email"):
            primary_owners = [
                BasicExpertInfo(
                    display_name=creator.get("name"),
                    email=creator.get("email"),
                )
            ]

        asset_urls = [
            str(url)
            for asset in assets
            if (url := asset.get("public_url") or asset.get("url"))
        ]

        metadata: dict[str, str] = {
            k: str(v)
            for k, v in {
                "workspace_id": workspace_id or None,
                "workspace_name": workspace_name,
                "board_id": board_id,
                "board_name": board_name,
                "group": group_title,
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "assets": ", ".join(asset_urls) if asset_urls else None,
                **_column_metadata(column_values),
            }.items()
            if v is not None and str(v)
        }

        external_access = self._get_item_external_access(
            board_id=board_id,
            item=item,
            add_prefix=False,
        )

        return Document(
            id=item_url,
            sections=cast(list[TextSection | ImageSection], sections),
            source=DocumentSource.MONDAY,
            semantic_identifier=item_name,
            title=item_name,
            doc_updated_at=time_str_to_utc(item.get("updated_at"))
            if item.get("updated_at")
            else None,
            primary_owners=primary_owners,
            doc_metadata={
                "hierarchy": {
                    "source_path": [workspace_name, board_name],
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "board_id": board_id,
                    "board_name": board_name,
                }
            },
            metadata=metadata,
            external_access=external_access,
        )

    def _append_items_to_batch(
        self,
        items: list[dict[str, Any]],
        board_context: BoardContext,
        start: datetime | None,
        end: datetime | None,
        batch: list[Document],
    ) -> Generator[list[Document], None, list[Document]]:
        for item in items:
            if not _item_in_time_window(item.get("updated_at"), start, end):
                continue

            batch.append(
                self._build_document(
                    item=item,
                    board_context=board_context,
                )
            )
            if len(batch) >= self.batch_size:
                yield batch
                batch = []

        return batch

    def _process_board_items(
        self,
        board_context: BoardContext,
        start: datetime | None,
        end: datetime | None,
        batch: list[Document],
    ) -> Generator[list[Document], None, list[Document]]:
        board_id = board_context["board_id"]
        board_response = self._run_query(
            _BOARD_ITEMS_PAGE_QUERY,
            {"boardId": board_id, "itemsLimit": _ITEMS_PAGE_LIMIT},
        ).get("boards", [])
        if not board_response:
            logger.warning("monday.com board %s was not returned by the API", board_id)
            return batch

        board = board_response[0]
        board_context = _merge_board_workspace_context(board_context, board)

        items_page = board.get("items_page") or {}
        items = items_page.get("items") or []
        batch = yield from self._append_items_to_batch(
            items=items,
            board_context=board_context,
            start=start,
            end=end,
            batch=batch,
        )

        cursor = items_page.get("cursor")
        while cursor:
            next_page = self._run_query(
                _NEXT_ITEMS_PAGE_QUERY,
                {"cursor": cursor, "itemsLimit": _ITEMS_PAGE_LIMIT},
            )["next_items_page"]
            items = next_page.get("items") or []
            batch = yield from self._append_items_to_batch(
                items=items,
                board_context=board_context,
                start=start,
                end=end,
                batch=batch,
            )
            cursor = next_page.get("cursor")

        return batch

    def _process_items(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> GenerateDocumentsOutput:
        batch: list[Document] = []

        for board_context in self._iter_board_contexts():
            batch = yield from self._process_board_items(
                board_context=board_context,
                start=start,
                end=end,
                batch=batch,
            )

        if batch:
            yield batch

    def _append_slim_items_to_batch(
        self,
        items: list[dict[str, Any]],
        board_id: str,
        start: datetime | None,
        end: datetime | None,
        batch: list[SlimDocument],
        *,
        include_permissions: bool,
    ) -> Generator[list[SlimDocument], None, list[SlimDocument]]:
        for item in items:
            if not _item_in_time_window(item.get("updated_at"), start, end):
                continue

            item_url = item.get("url") or f"monday__{item['id']}"
            external_access = (
                self._get_item_external_access(
                    board_id=board_id,
                    item=item,
                    add_prefix=False,
                )
                if include_permissions
                else None
            )
            batch.append(SlimDocument(id=item_url, external_access=external_access))
            if len(batch) >= self.batch_size:
                yield batch
                batch = []

        return batch

    def _process_board_slim_items(
        self,
        board_id: str,
        start: datetime | None,
        end: datetime | None,
        batch: list[SlimDocument],
        *,
        include_permissions: bool,
    ) -> Generator[list[SlimDocument], None, list[SlimDocument]]:
        board_response = self._run_query(
            _SLIM_BOARD_ITEMS_PAGE_QUERY,
            {"boardId": board_id, "itemsLimit": _ITEMS_PAGE_LIMIT},
        ).get("boards", [])
        if not board_response:
            logger.warning("monday.com board %s was not returned by the API", board_id)
            return batch

        items_page = board_response[0].get("items_page") or {}
        items = items_page.get("items") or []
        batch = yield from self._append_slim_items_to_batch(
            items=items,
            board_id=board_id,
            start=start,
            end=end,
            batch=batch,
            include_permissions=include_permissions,
        )

        cursor = items_page.get("cursor")
        while cursor:
            next_page = self._run_query(
                _SLIM_NEXT_ITEMS_PAGE_QUERY,
                {"cursor": cursor, "itemsLimit": _ITEMS_PAGE_LIMIT},
            )["next_items_page"]
            items = next_page.get("items") or []
            batch = yield from self._append_slim_items_to_batch(
                items=items,
                board_id=board_id,
                start=start,
                end=end,
                batch=batch,
                include_permissions=include_permissions,
            )
            cursor = next_page.get("cursor")

        return batch

    def _process_slim_items(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        include_permissions: bool,
    ) -> GenerateSlimDocumentOutput:
        self._board_access_data_cache.clear()
        batch: list[SlimDocument] = []

        for board_context in self._iter_board_contexts():
            batch = yield from self._process_board_slim_items(
                board_id=board_context["board_id"],
                start=start,
                end=end,
                batch=batch,
                include_permissions=include_permissions,
            )

        if batch:
            yield batch

    def retrieve_all_slim_docs(
        self,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
        callback: IndexingHeartbeatInterface | None = None,  # noqa: ARG002
    ) -> GenerateSlimDocumentOutput:
        start_time = (
            datetime.fromtimestamp(start, tz=timezone.utc)
            if start is not None
            else None
        )
        end_time = (
            datetime.fromtimestamp(end, tz=timezone.utc) if end is not None else None
        )
        yield from self._process_slim_items(
            start=start_time,
            end=end_time,
            include_permissions=False,
        )

    def retrieve_all_slim_docs_perm_sync(
        self,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
        callback: IndexingHeartbeatInterface | None = None,  # noqa: ARG002
    ) -> GenerateSlimDocumentOutput:
        start_time = (
            datetime.fromtimestamp(start, tz=timezone.utc)
            if start is not None
            else None
        )
        end_time = (
            datetime.fromtimestamp(end, tz=timezone.utc) if end is not None else None
        )
        yield from self._process_slim_items(
            start=start_time,
            end=end_time,
            include_permissions=True,
        )

    def load_from_state(self) -> GenerateDocumentsOutput:
        yield from self._process_items()

    def poll_source(
        self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch
    ) -> GenerateDocumentsOutput:
        start_time = datetime.fromtimestamp(start, tz=timezone.utc)
        end_time = datetime.fromtimestamp(end, tz=timezone.utc)
        yield from self._process_items(start=start_time, end=end_time)


if __name__ == "__main__":
    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": os.environ["MONDAY_API_TOKEN"]})
    connector.validate_connector_settings()
    print(next(connector.load_from_state()))
