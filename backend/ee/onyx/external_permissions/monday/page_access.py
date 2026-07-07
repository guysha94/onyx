from collections.abc import Callable
from typing import Any

from onyx.access.models import ExternalAccess
from onyx.utils.logger import setup_logger

logger = setup_logger()

_BOARD_ACCESS_QUERY = """
query MondayBoardAccess($boardId: ID!) {
    boards(ids: [$boardId]) {
        id
        board_kind
        permissions
        owners {
            email
        }
        subscribers {
            email
        }
        team_owners(limit: 100, page: 1) {
            id
            users(limit: 100, page: 1) {
                email
            }
        }
        team_subscribers(limit: 100, page: 1) {
            id
            users(limit: 100, page: 1) {
                email
            }
        }
        workspace {
            id
            kind
            users_subscribers(limit: 100, page: 1) {
                email
            }
            owners_subscribers(limit: 100, page: 1) {
                email
            }
            teams_subscribers(limit: 100, page: 1) {
                id
                users(limit: 100, page: 1) {
                    email
                }
            }
        }
    }
}
"""


def _user_emails(users: list[dict[str, Any]] | None) -> set[str]:
    if not users:
        return set()
    return {email for user in users if (email := user.get("email"))}


def _team_user_emails(teams: list[dict[str, Any]] | None) -> set[str]:
    emails: set[str] = set()
    if not teams:
        return emails
    for team in teams:
        for user in team.get("users") or []:
            if email := user.get("email"):
                emails.add(email)
    return emails


def build_external_access_from_board(
    board: dict[str, Any],
    item: dict[str, Any] | None = None,
) -> ExternalAccess:
    """
    Resolve Monday.com board permissions into an ExternalAccess object.

    Monday ACL is board-scoped: items inherit their parent board's permission
    model, with optional refinement for assignee-scoped boards via item
    subscribers.
    """
    board_kind = (board.get("board_kind") or "").lower()
    permissions = (board.get("permissions") or "everyone").lower()
    workspace = board.get("workspace") or {}
    workspace_kind = (workspace.get("kind") or "open").lower()

    if (
        board_kind == "public"
        and workspace_kind == "open"
        and permissions == "everyone"
    ):
        return ExternalAccess.public()

    emails: set[str] = set()
    emails |= _user_emails(board.get("owners"))
    emails |= _team_user_emails(board.get("team_owners"))

    if permissions == "owners":
        pass
    elif permissions in {"collaborators", "everyone"}:
        emails |= _user_emails(board.get("subscribers"))
        emails |= _team_user_emails(board.get("team_subscribers"))
    elif permissions == "assignee":
        if item:
            emails |= _user_emails(item.get("subscribers"))

    if board_kind == "public" and workspace_kind == "closed":
        emails |= _user_emails(workspace.get("users_subscribers"))
        emails |= _user_emails(workspace.get("owners_subscribers"))
        emails |= _team_user_emails(workspace.get("teams_subscribers"))

    if board_kind in {"private", "share"} and permissions != "owners":
        emails |= _user_emails(board.get("subscribers"))
        emails |= _team_user_emails(board.get("team_subscribers"))

    if not emails:
        logger.warning(
            "Monday board %s resolved to empty private ExternalAccess; "
            "board_kind=%s permissions=%s workspace_kind=%s",
            board.get("id"),
            board_kind,
            permissions,
            workspace_kind,
        )

    return ExternalAccess(
        external_user_emails=emails,
        external_user_group_ids=set(),
        is_public=False,
    )


def get_board_permissions(
    run_query: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    board_id: str,
    item: dict[str, Any] | None = None,
    add_prefix: bool = False,  # noqa: ARG001
    board_data: dict[str, Any] | None = None,
) -> ExternalAccess | None:
    """
    Fetch and resolve permissions for a Monday.com board.

    Team members are expanded to user emails inline (no separate group sync).
    """
    if board_data is None:
        boards = run_query(_BOARD_ACCESS_QUERY, {"boardId": board_id}).get(
            "boards", []
        )
        board_data = boards[0] if boards else None

    if not board_data:
        logger.warning("Monday board %s was not returned by access query", board_id)
        return ExternalAccess.empty()

    return build_external_access_from_board(board_data, item=item)
