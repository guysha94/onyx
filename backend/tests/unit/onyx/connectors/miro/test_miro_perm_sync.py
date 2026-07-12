"""Unit tests for Miro Auto Sync Permissions (FORK: miro).

Covers the team-based ACL logic on the connector: board sharing-policy ->
ExternalAccess, the team-member -> email id-space join, and slim-doc perm sync.
All Miro API access is mocked, so these are pure unit tests.
"""
from typing import Any
from unittest.mock import patch

from onyx.access.models import ExternalAccess
from onyx.connectors.miro.connector import MiroConnector
from onyx.connectors.miro.connector import team_group_id


def _connector() -> MiroConnector:
    connector = MiroConnector(miro_org_id="org1")
    connector.miro_access_token = "token"
    return connector


def _sharing(access: str, org_access: str, team_access: str) -> dict[str, Any]:
    return {
        "access": access,
        "organizationAccess": org_access,
        "teamAccess": team_access,
    }


def test_board_external_access_org_wide_is_public() -> None:
    board = {"team": {"id": "t1"}, "sharingPolicy": _sharing("private", "view", "edit")}
    assert MiroConnector._board_external_access(board) == ExternalAccess.public()


def test_board_external_access_public_link_is_public() -> None:
    board = {
        "team": {"id": "t1"},
        "sharingPolicy": _sharing("edit", "private", "private"),
    }
    assert MiroConnector._board_external_access(board) == ExternalAccess.public()


def test_board_external_access_team_maps_to_team_group() -> None:
    board = {
        "team": {"id": "t1"},
        "sharingPolicy": _sharing("private", "private", "edit"),
    }
    access = MiroConnector._board_external_access(board)
    assert access.is_public is False
    assert access.external_user_emails == set()
    assert access.external_user_group_ids == {team_group_id("t1")}


def test_board_external_access_private_is_admin_only() -> None:
    board = {
        "team": {"id": "t1"},
        "sharingPolicy": _sharing("private", "private", "no_access"),
    }
    assert MiroConnector._board_external_access(board) == ExternalAccess.empty()


def test_board_external_access_reads_nested_policy() -> None:
    # Some board payloads only carry policy.sharingPolicy (no top-level key).
    board = {
        "team": {"id": "t9"},
        "policy": {"sharingPolicy": _sharing("private", "private", "view")},
    }
    access = MiroConnector._board_external_access(board)
    assert access.external_user_group_ids == {team_group_id("t9")}


def test_get_team_member_email_map_joins_ids_to_emails() -> None:
    connector = _connector()

    def fake_get_json(
        url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if url.endswith("/orgs/org1/members"):
            return {
                "data": [
                    {"id": "m1", "email": "a@x.com"},
                    {"id": "m2", "email": "b@x.com"},
                    {"id": "m3"},  # no email -> not mappable
                ]
            }
        if url.endswith("/orgs/org1/teams"):
            return {"data": [{"id": "t1"}, {"id": "t2"}]}
        if url.endswith("/orgs/org1/teams/t1/members"):
            return {"data": [{"memberId": "m1"}, {"memberId": "m3"}]}
        if url.endswith("/orgs/org1/teams/t2/members"):
            return {"data": [{"id": "m2"}, {"memberId": "unknown"}]}
        raise AssertionError(f"unexpected url {url}")

    with patch.object(MiroConnector, "_get_json", side_effect=fake_get_json):
        result = connector.get_team_member_email_map()

    assert result == {"t1": {"a@x.com"}, "t2": {"b@x.com"}}


def test_retrieve_all_slim_docs_perm_sync_maps_board_access() -> None:
    connector = _connector()

    boards = [
        {"id": "b1", "team": {"id": "t1"}, "sharingPolicy": _sharing("private", "private", "edit")},
        {"id": "b2", "team": {"id": "t2"}, "sharingPolicy": _sharing("private", "private", "no_access")},
    ]
    items_by_board: dict[str, list[dict[str, Any]]] = {
        "b1": [
            {"id": "i1", "type": "image", "data": {"imageUrl": "u"}},
            {"id": "i2", "type": "text", "data": {"content": "x"}},  # not an image
            {"id": "i3", "type": "image", "data": {}},  # no imageUrl
        ],
        "b2": [
            {"id": "i4", "type": "image", "data": {"imageUrl": "u"}},
        ],
    }

    with patch.object(
        MiroConnector, "_iter_boards", return_value=iter(boards)
    ), patch.object(
        MiroConnector,
        "_iter_board_items",
        side_effect=lambda board_id: iter(items_by_board[board_id]),
    ):
        batches = list(connector.retrieve_all_slim_docs_perm_sync())

    docs = {doc.id: doc for batch in batches for doc in batch}

    # Only captionable image items are yielded (matches indexing).
    assert set(docs) == {"miro__b1__i1", "miro__b2__i4"}
    assert docs["miro__b1__i1"].external_access is not None
    assert docs["miro__b1__i1"].external_access.external_user_group_ids == {
        team_group_id("t1")
    }
    assert docs["miro__b2__i4"].external_access == ExternalAccess.empty()
