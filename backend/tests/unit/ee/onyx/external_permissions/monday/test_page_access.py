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


def test_build_external_access_closed_workspace_private_board() -> None:
    board = {
        "id": "6302069973",
        "board_kind": "private",
        "permissions": "everyone",
        "owners": [],
        "subscribers": [],
        "team_owners": [],
        "team_subscribers": [],
        "workspace": {
            "kind": "closed",
            "users_subscribers": [{"email": "member@example.com"}],
            "owners_subscribers": [],
            "teams_subscribers": [],
        },
    }
    access = build_external_access_from_board(board)
    assert access.is_public is False
    assert access.external_user_emails == {"member@example.com"}


def test_fetch_board_access_data_paginates_subscribers() -> None:
    from ee.onyx.external_permissions.monday.page_access import fetch_board_access_data

    page_responses = {
        1: {
            "boards": [
                {
                    "id": "1",
                    "board_kind": "public",
                    "permissions": "collaborators",
                    "owners": [],
                    "subscribers": [],
                    "team_owners": [
                        {"id": "t1", "users": [{"email": "a@example.com"}]}
                    ],
                    "team_subscribers": [],
                    "workspace": {
                        "kind": "closed",
                        "users_subscribers": [
                            {"email": f"user{i}@example.com"} for i in range(100)
                        ],
                        "owners_subscribers": [],
                        "teams_subscribers": [],
                    },
                }
            ]
        },
        2: {
            "boards": [
                {
                    "id": "1",
                    "board_kind": "public",
                    "permissions": "collaborators",
                    "owners": [],
                    "subscribers": [],
                    "team_owners": [],
                    "team_subscribers": [],
                    "workspace": {
                        "kind": "closed",
                        "users_subscribers": [{"email": "page2@example.com"}],
                        "owners_subscribers": [],
                        "teams_subscribers": [],
                    },
                }
            ]
        },
    }

    def run_query(
        _query: str, variables: dict[str, object] | None
    ) -> dict[str, object]:
        page = (variables or {}).get("page", 1)
        return page_responses[page]

    board_data = fetch_board_access_data(run_query, "1")

    assert board_data is not None
    workspace = board_data["workspace"]
    assert len(workspace["users_subscribers"]) == 101
    assert workspace["users_subscribers"][-1]["email"] == "page2@example.com"
