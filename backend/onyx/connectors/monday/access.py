"""
Permission / access-control logic for Monday.com boards and items.
"""

from collections.abc import Callable
from typing import Any
from typing import cast

from onyx.access.models import ExternalAccess
from onyx.utils.variable_functionality import fetch_versioned_implementation
from onyx.utils.variable_functionality import global_version


def build_external_access_for_board(
    board: dict[str, Any],
    item: dict[str, Any] | None = None,
) -> ExternalAccess | None:
    """Build ExternalAccess from a pre-fetched Monday board payload. Requires EE."""
    if not global_version.is_ee_version():
        return None

    ee_build = cast(
        Callable[[dict[str, Any], dict[str, Any] | None], ExternalAccess],
        fetch_versioned_implementation(
            "onyx.external_permissions.monday.page_access",
            "build_external_access_from_board",
        ),
    )
    return ee_build(board, item)


def get_board_permissions(
    run_query: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    board_id: str,
    item: dict[str, Any] | None = None,
    add_prefix: bool = False,
    board_data: dict[str, Any] | None = None,
) -> ExternalAccess | None:
    """
    Fetch board-level permissions for Monday.com items.
    Requires Enterprise Edition.
    """
    if not global_version.is_ee_version():
        return None

    ee_get_board_permissions = cast(
        Callable[
            [
                Callable[[str, dict[str, Any] | None], dict[str, Any]],
                str,
                dict[str, Any] | None,
                bool,
                dict[str, Any] | None,
            ],
            ExternalAccess | None,
        ],
        fetch_versioned_implementation(
            "onyx.external_permissions.monday.page_access", "get_board_permissions"
        ),
    )

    return ee_get_board_permissions(
        run_query, board_id, item, add_prefix, board_data
    )
