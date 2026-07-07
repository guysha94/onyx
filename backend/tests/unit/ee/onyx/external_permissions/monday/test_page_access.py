from onyx.access.models import ExternalAccess
from ee.onyx.external_permissions.monday.page_access import (
    build_external_access_from_board,
)


def test_build_external_access_public_board() -> None:
    board = {
        "id": "123",
        "board_kind": "public",
        "permissions": "everyone",
        "workspace": {"kind": "open"},
    }
    access = build_external_access_from_board(board)
    assert access == ExternalAccess.public()


def test_build_external_access_private_board_subscribers() -> None:
    board = {
        "id": "123",
        "board_kind": "private",
        "permissions": "collaborators",
        "owners": [{"email": "owner@example.com"}],
        "subscribers": [{"email": "sub@example.com"}],
        "team_owners": [],
        "team_subscribers": [],
        "workspace": {"kind": "open"},
    }
    access = build_external_access_from_board(board)
    assert access.is_public is False
    assert access.external_user_emails == {"owner@example.com", "sub@example.com"}


def test_build_external_access_assignee_includes_item_subscribers() -> None:
    board = {
        "id": "123",
        "board_kind": "private",
        "permissions": "assignee",
        "owners": [{"email": "owner@example.com"}],
        "subscribers": [{"email": "sub@example.com"}],
        "team_owners": [],
        "team_subscribers": [],
        "workspace": {"kind": "open"},
    }
    item = {"subscribers": [{"email": "assignee@example.com"}]}
    access = build_external_access_from_board(board, item=item)
    assert access.external_user_emails == {
        "owner@example.com",
        "assignee@example.com",
    }
