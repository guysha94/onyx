import json
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from onyx.access.models import ExternalAccess
from onyx.configs.constants import DocumentSource
from onyx.connectors.exceptions import ConnectorValidationError
from onyx.connectors.cross_connector_utils.miscellaneous_utils import (
    get_metadata_keys_to_ignore,
)
from onyx.connectors.exceptions import CredentialExpiredError
from onyx.connectors.exceptions import InsufficientPermissionsError
from onyx.connectors.monday.client import MondayApiClient
from onyx.connectors.monday.connector import BoardContext
from onyx.connectors.monday.connector import MondayConnector
from onyx.connectors.monday.connector import _hierarchy_context_blurb
from onyx.connectors.monday.connector import _normalize_id_filter


def test_normalize_id_filter_empty_list_becomes_none() -> None:
    assert _normalize_id_filter([]) is None


def test_normalize_id_filter_none_stays_none() -> None:
    assert _normalize_id_filter(None) is None


def test_normalize_id_filter_preserves_values() -> None:
    assert _normalize_id_filter(["123", "456"]) == ["123", "456"]


def test_hierarchy_context_blurb() -> None:
    assert _hierarchy_context_blurb("Marketing", "Sprint Board") == (
        "Workspace: Marketing\nBoard: Sprint Board"
    )


def test_build_document_includes_hierarchy_metadata_and_context() -> None:
    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": "token"})

    board_context = BoardContext(
        board_id="200",
        board_name="Sprint Board",
        workspace_id="100",
        workspace_name="Marketing",
    )
    item: dict[str, Any] = {
        "id": "300",
        "name": "Fix login bug",
        "url": "https://acme.monday.com/boards/200/pulses/300",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-02-01T00:00:00Z",
        "group": {"title": "In Progress"},
        "column_values": [
            {
                "id": "status",
                "text": "Working on it",
                "column": {"title": "Status"},
            }
        ],
        "updates": [],
        "assets": [],
        "creator": {"name": "Alice", "email": "alice@example.com"},
    }

    document = connector._build_document(item=item, board_context=board_context)

    assert document.metadata["workspace_id"] == "100"
    assert document.metadata["workspace_name"] == "Marketing"
    assert document.metadata["board_id"] == "200"
    assert document.metadata["board_name"] == "Sprint Board"
    assert document.metadata["group"] == "In Progress"
    assert document.metadata["Status"] == "Working on it"

    hierarchy = document.doc_metadata["hierarchy"]
    assert hierarchy["source_path"] == ["Marketing", "Sprint Board"]
    assert hierarchy["workspace_id"] == "100"
    assert hierarchy["workspace_name"] == "Marketing"
    assert hierarchy["board_id"] == "200"
    assert hierarchy["board_name"] == "Sprint Board"

    section_text = document.sections[0].text
    assert section_text is not None
    assert section_text.startswith("Workspace: Marketing\nBoard: Sprint Board")
    assert "Fix login bug" in section_text


def test_monday_metadata_keys_to_ignore() -> None:
    ignored = get_metadata_keys_to_ignore(DocumentSource.MONDAY)
    assert "workspace_id" in ignored
    assert "board_id" in ignored
    assert "workspace_name" not in ignored
    assert "board_name" not in ignored


def _mock_http_response(status: int, body: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.data = json.dumps(body).encode("utf-8")
    return response


def test_monday_api_client_run_query_success() -> None:
    client = MondayApiClient("token")
    response_body = {"data": {"me": {"id": "1"}}}

    with patch.object(
        client._http, "request", return_value=_mock_http_response(200, response_body)
    ):
        data = client.run_query("query { me { id } }")

    assert data == {"me": {"id": "1"}}


def test_monday_api_client_run_query_401() -> None:
    client = MondayApiClient("token")

    with patch.object(
        client._http,
        "request",
        return_value=_mock_http_response(401, {"error": "unauthorized"}),
    ):
        with pytest.raises(CredentialExpiredError):
            client.run_query("query { me { id } }")


def test_monday_api_client_run_query_403() -> None:
    client = MondayApiClient("token")

    with patch.object(
        client._http,
        "request",
        return_value=_mock_http_response(403, {"error": "forbidden"}),
    ):
        with pytest.raises(InsufficientPermissionsError):
            client.run_query("query { me { id } }")


def test_monday_api_client_run_query_graphql_error() -> None:
    client = MondayApiClient("token")
    response_body = {"errors": [{"message": "Invalid query"}]}

    with patch.object(
        client._http, "request", return_value=_mock_http_response(200, response_body)
    ):
        with pytest.raises(RuntimeError, match="monday.com GraphQL error"):
            client.run_query("query { bad }")


def test_monday_api_client_list_workspaces() -> None:
    client = MondayApiClient("token")
    client._sdk.workspaces.get_workspaces = MagicMock(
        return_value={"data": {"workspaces": [{"id": "1", "name": "Main"}]}}
    )

    workspaces = client.list_workspaces()

    assert workspaces == [{"id": "1", "name": "Main"}]


def test_monday_api_client_list_boards_page() -> None:
    client = MondayApiClient("token")
    client._sdk.boards.fetch_boards = MagicMock(
        return_value={"data": {"boards": [{"id": "10", "name": "Roadmap"}]}}
    )

    boards = client.list_boards_page(limit=50, page=1, workspace_ids=[1])

    assert boards == [{"id": "10", "name": "Roadmap"}]
    client._sdk.boards.fetch_boards.assert_called_once_with(
        limit=50,
        page=1,
        workspace_ids=[1],
        ids=None,
    )


def test_build_document_sets_external_access() -> None:
    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": "token"})
    connector._board_access_data_cache["200"] = {
        "id": "200",
        "board_kind": "private",
        "permissions": "collaborators",
        "owners": [{"email": "owner@example.com"}],
        "subscribers": [{"email": "member@example.com"}],
        "team_owners": [],
        "team_subscribers": [],
        "workspace": {"kind": "closed", "users_subscribers": []},
    }

    with patch(
        "onyx.connectors.monday.connector.get_board_permissions",
        return_value=ExternalAccess(
            external_user_emails={"member@example.com"},
            external_user_group_ids=set(),
            is_public=False,
        ),
    ):
        document = connector._build_document(
            item={
                "id": "300",
                "name": "Task",
                "url": "https://acme.monday.com/boards/200/pulses/300",
                "column_values": [],
                "updates": [],
                "assets": [],
            },
            board_context=BoardContext(
                board_id="200",
                board_name="General Tasks",
                workspace_id="100",
                workspace_name="AI R&D",
            ),
        )

    assert document.external_access is not None
    assert document.external_access.external_user_emails == {"member@example.com"}


def test_validate_perm_sync_rejects_empty_private_acl() -> None:
    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": "token"})
    connector._iter_board_contexts = MagicMock(
        return_value=iter(
            [
                BoardContext(
                    board_id="200",
                    board_name="General Tasks",
                    workspace_id="100",
                    workspace_name="AI R&D",
                )
            ]
        )
    )

    with patch(
        "onyx.connectors.monday.connector.get_board_permissions",
        return_value=ExternalAccess(
            external_user_emails=set(),
            external_user_group_ids=set(),
            is_public=False,
        ),
    ):
        with pytest.raises(ConnectorValidationError, match="empty private ACL"):
            connector.validate_perm_sync()


def test_validate_perm_sync_rejects_no_boards() -> None:
    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": "token"})
    connector._iter_board_contexts = MagicMock(return_value=iter([]))

    with pytest.raises(ConnectorValidationError, match="could not find any accessible boards"):
        connector.validate_perm_sync()


def test_validate_perm_sync_unscoped_stops_after_first_board() -> None:
    """Unscoped validate_perm_sync must not paginate every board.

    Materializing all boards() pages can exceed the frontend connector-creation
    timeout and race-delete the connector before credential link completes.
    """
    from onyx.connectors.monday.connector import _LIST_ALL_BOARDS_QUERY

    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": "token"})
    connector._client = MagicMock()

    # First page returns a full page (would normally continue); validation must
    # stop after consuming the first board without requesting page 2.
    page_one_boards = [
        {
            "id": str(i),
            "name": f"Board {i}",
            "workspace": {"id": "1", "name": "Workspace"},
        }
        for i in range(1, 51)
    ]
    connector._client.run_query.return_value = {"boards": page_one_boards}

    with patch(
        "onyx.connectors.monday.connector.get_board_permissions",
        return_value=ExternalAccess(
            external_user_emails={"user@example.com"},
            external_user_group_ids=set(),
            is_public=False,
        ),
    ) as mock_get_perms:
        connector.validate_perm_sync()

    connector._client.run_query.assert_called_once()
    call_args = connector._client.run_query.call_args
    assert call_args[0][0] == _LIST_ALL_BOARDS_QUERY
    assert call_args[0][1] == {"boardsLimit": 50, "page": 1}
    mock_get_perms.assert_called_once()
    assert mock_get_perms.call_args[0][1] == "1"


def test_iter_board_contexts_uses_board_workspace_name() -> None:
    connector = MondayConnector(workspace_ids=["5485895"])
    connector.load_credentials({"monday_api_token": "token"})

    # list_workspaces misses the closed workspace (common with API tokens),
    # so name resolution must come from the board payload.
    connector._client = MagicMock()
    connector._client.list_workspaces.return_value = []
    connector._client.run_query.return_value = {
        "boards": [
            {
                "id": "6302069973",
                "name": "General Tasks",
                "workspace": {"id": "5485895", "name": "AI R&D"},
            }
        ]
    }

    contexts = list(connector._iter_board_contexts())

    assert len(contexts) == 1
    assert contexts[0]["workspace_id"] == "5485895"
    assert contexts[0]["workspace_name"] == "AI R&D"
    assert contexts[0]["board_id"] == "6302069973"
    assert contexts[0]["board_name"] == "General Tasks"


def test_iter_board_contexts_empty_filters_uses_unfiltered_boards_query() -> None:
    """Empty workspace/board filters must discover boards without get_workspaces.

    Closed workspaces (e.g. AI R&D) are often missing from list_workspaces for
    API tokens; unfiltered boards() still returns those boards with workspace
    metadata on the payload.
    """
    from onyx.connectors.monday.connector import _LIST_ALL_BOARDS_QUERY

    connector = MondayConnector()
    connector.load_credentials({"monday_api_token": "token"})
    connector._client = MagicMock()
    connector._client.list_workspaces.return_value = []
    connector._client.run_query.return_value = {
        "boards": [
            {
                "id": "6302069973",
                "name": "General Tasks",
                "workspace": {"id": "5485895", "name": "AI R&D"},
            },
            {
                "id": "99",
                "name": "Other Board",
                "workspace": {"id": "1", "name": "Open Workspace"},
            },
        ]
    }

    contexts = list(connector._iter_board_contexts())

    assert len(contexts) == 2
    assert contexts[0]["workspace_id"] == "5485895"
    assert contexts[0]["workspace_name"] == "AI R&D"
    assert contexts[0]["board_id"] == "6302069973"
    assert contexts[1]["workspace_name"] == "Open Workspace"

    connector._client.list_workspaces.assert_not_called()
    connector._client.run_query.assert_called_once()
    call_args = connector._client.run_query.call_args
    assert call_args[0][0] == _LIST_ALL_BOARDS_QUERY
    assert call_args[0][1] == {"boardsLimit": 50, "page": 1}


def test_merge_board_workspace_context_overrides_fallback_name() -> None:
    from onyx.connectors.monday.connector import _merge_board_workspace_context

    fallback = BoardContext(
        board_id="6302069973",
        board_name="General Tasks",
        workspace_id="5485895",
        workspace_name="Workspace 5485895",
    )
    board = {
        "id": "6302069973",
        "name": "General Tasks",
        "workspace": {"id": "5485895", "name": "AI R&D"},
    }

    merged = _merge_board_workspace_context(fallback, board)

    assert merged["workspace_name"] == "AI R&D"
    assert merged["workspace_id"] == "5485895"
