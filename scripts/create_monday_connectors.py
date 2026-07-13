"""Bulk-create Onyx Monday.com connectors — one cc_pair per workspace (namespace).

Fetches all accessible Monday workspaces via GraphQL and creates a connector for
each workspace not already present in connectors.json (see existing monday entries).

Steps per workspace:
  1. POST /manage/admin/connector
  2. PUT  /manage/connector/{id}/credential/{credential_id}

Usage:
  export ONYX_PAT="onyx_pat_..."
  export MONDAY_API_TOKEN="..."
  uv run create_monday_connectors.py --dry-run
  uv run create_monday_connectors.py

Workspace list sources (first match wins):
  1. --workspaces-file path.json
  2. monday-workspaces.json next to this script
  3. Live Monday GraphQL API (requires MONDAY_API_TOKEN)

Optional env vars:
  ONYX_API_URL        default: https://sp-ai-platform.superplay.dev/api/manage
  ONYX_CREDENTIAL_ID  default: 25 (Monday credential)

Workspaces with no accessible boards are skipped by default — sync access_type
requires at least one board for Monday permission-sync validation on link.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONNECTORS_JSON = SCRIPT_DIR / "connectors.json"
DEFAULT_WORKSPACES_MANIFEST = SCRIPT_DIR / "monday-workspaces.json"

BASE_URL = os.environ.get(
    "ONYX_API_URL", ""
)
ONYX_PAT = os.environ.get("ONYX_PAT", "")
CREDENTIAL_ID = int(os.environ.get("ONYX_CREDENTIAL_ID", "25"))
MONDAY_API_TOKEN = os.environ.get("MONDAY_API_TOKEN", "")

MONDAY_GRAPHQL_URL = "https://api.monday.com/v2"
MONDAY_API_VERSION = "2025-10"
WORKSPACES_PAGE_LIMIT = 100

REFRESH_FREQ = 1800
PRUNE_FREQ = 604800

_LIST_WORKSPACES_QUERY = """
query MondayListWorkspaces($limit: Int!, $page: Int!) {
  workspaces(limit: $limit, page: $page, state: active) {
    id
    name
    kind
    state
  }
}
"""

_BOARDS_PAGE_LIMIT = 50

_LIST_BOARD_WORKSPACES_QUERY = """
query MondayBoardWorkspaces($boardsLimit: Int!, $page: Int!) {
  boards(limit: $boardsLimit, page: $page) {
    workspace {
      id
    }
  }
}
"""


def load_existing_workspace_ids(connectors_path: Path) -> set[str]:
    data: list[dict[str, Any]] = json.loads(connectors_path.read_text())
    existing: set[str] = set()
    for connector in data:
        if connector.get("source") != "monday":
            continue
        cfg = connector.get("connector_specific_config") or {}
        for workspace_id in cfg.get("workspace_ids") or []:
            workspace_id = str(workspace_id).strip()
            if workspace_id:
                existing.add(workspace_id)
    return existing


def load_existing_connector_names(connectors_path: Path) -> set[str]:
    data: list[dict[str, Any]] = json.loads(connectors_path.read_text())
    names: set[str] = set()
    for connector in data:
        if connector.get("source") != "monday":
            continue
        name = str(connector.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def load_workspaces_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def monday_headers() -> dict[str, str]:
    return {
        "Authorization": MONDAY_API_TOKEN,
        "API-Version": MONDAY_API_VERSION,
        "Content-Type": "application/json",
    }


def run_monday_query(
    client: httpx.Client, query: str, variables: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(
        MONDAY_GRAPHQL_URL,
        json={"query": query, "variables": variables},
    )
    if not response.is_success:
        raise RuntimeError(f"Monday API HTTP {response.status_code}: {response.text}")

    payload = response.json()
    if errors := payload.get("errors"):
        messages = "; ".join(str(error.get("message", error)) for error in errors)
        raise RuntimeError(f"Monday GraphQL error: {messages}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Monday GraphQL response missing data: {payload}")
    return data


def fetch_monday_workspaces(client: httpx.Client) -> list[dict[str, Any]]:
    workspaces: list[dict[str, Any]] = []
    page = 1

    while True:
        data = run_monday_query(
            client,
            _LIST_WORKSPACES_QUERY,
            {"limit": WORKSPACES_PAGE_LIMIT, "page": page},
        )
        batch = data.get("workspaces") or []
        if not batch:
            break
        workspaces.extend(batch)
        if len(batch) < WORKSPACES_PAGE_LIMIT:
            break
        page += 1

    return workspaces


def fetch_workspace_ids_with_boards(client: httpx.Client) -> set[str]:
    """Return workspace IDs that have at least one accessible board."""
    workspace_ids: set[str] = set()
    page = 1

    while True:
        data = run_monday_query(
            client,
            _LIST_BOARD_WORKSPACES_QUERY,
            {"boardsLimit": _BOARDS_PAGE_LIMIT, "page": page},
        )
        batch = data.get("boards") or []
        if not batch:
            break

        for board in batch:
            workspace = board.get("workspace") or {}
            workspace_id = str(workspace.get("id") or "").strip()
            if workspace_id:
                workspace_ids.add(workspace_id)

        if len(batch) < _BOARDS_PAGE_LIMIT:
            break
        page += 1

    return workspace_ids


def resolve_workspaces(
    *,
    workspaces_file: Path | None,
    fetch_from_monday: bool,
) -> list[dict[str, Any]]:
    if workspaces_file is not None:
        if not workspaces_file.is_file():
            raise FileNotFoundError(f"workspaces manifest not found: {workspaces_file}")
        return load_workspaces_manifest(workspaces_file)

    if not fetch_from_monday and DEFAULT_WORKSPACES_MANIFEST.is_file():
        return load_workspaces_manifest(DEFAULT_WORKSPACES_MANIFEST)

    if not MONDAY_API_TOKEN:
        raise RuntimeError(
            "MONDAY_API_TOKEN is required for live Monday fetch "
            f"(or provide --workspaces-file / {DEFAULT_WORKSPACES_MANIFEST.name})"
        )

    with httpx.Client(headers=monday_headers(), timeout=120) as monday_client:
        return fetch_monday_workspaces(monday_client)


def unique_connector_name(
    workspace_name: str,
    workspace_id: str,
    taken_names: set[str],
) -> str:
    base = workspace_name.strip() or f"monday-workspace-{workspace_id}"
    if base not in taken_names:
        return base

    suffix = f" ({workspace_id})"
    candidate = f"{base}{suffix}"
    if candidate not in taken_names:
        return candidate

    counter = 2
    while True:
        candidate = f"{base}{suffix}-{counter}"
        if candidate not in taken_names:
            return candidate
        counter += 1


def select_workspaces_to_create(
    workspaces: list[dict[str, Any]],
    existing_workspace_ids: set[str],
    *,
    skip_templates: bool,
    workspace_ids_with_boards: set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped_no_boards: list[dict[str, Any]] = []
    for workspace in workspaces:
        workspace_id = str(workspace.get("id") or "").strip()
        if not workspace_id:
            continue
        if workspace_id in existing_workspace_ids:
            continue
        if skip_templates and str(workspace.get("kind") or "").lower() == "template":
            continue
        if (
            workspace_ids_with_boards is not None
            and workspace_id not in workspace_ids_with_boards
        ):
            skipped_no_boards.append(workspace)
            continue
        selected.append(workspace)

    def _workspace_sort_key(ws: dict[str, Any]) -> tuple[str, str]:
        return (str(ws.get("name") or "").lower(), str(ws.get("id") or ""))

    return sorted(selected, key=_workspace_sort_key), sorted(
        skipped_no_boards, key=_workspace_sort_key
    )


def build_connector_payload(name: str, workspace_id: str) -> dict[str, Any]:
    return {
        "name": name,
        "access_type": "sync",
        "source": "monday",
        "input_type": "poll",
        "refresh_freq": REFRESH_FREQ,
        "prune_freq": PRUNE_FREQ,
        "indexing_start": None,
        "connector_specific_config": {
            "board_ids": [],
            "workspace_ids": [workspace_id],
        },
    }


def delete_connector(client: httpx.Client, connector_id: int) -> None:
    response = client.delete(f"{BASE_URL}/admin/connector/{connector_id}")
    if not response.is_success:
        raise RuntimeError(
            f"failed to delete orphan connector id={connector_id}: {response.text}"
        )


def create_and_link(
    client: httpx.Client, payload: dict[str, Any], credential_id: int
) -> tuple[int, int]:
    create_resp = client.post(f"{BASE_URL}/admin/connector", json=payload)
    if not create_resp.is_success:
        raise RuntimeError(f"create failed for {payload['name']}: {create_resp.text}")

    connector_id = create_resp.json()["id"]
    link_body = {
        "name": payload["name"],
        "access_type": payload["access_type"],
        "groups": [],
    }
    link_resp = client.put(
        f"{BASE_URL}/connector/{connector_id}/credential/{credential_id}",
        json=link_body,
    )
    if not link_resp.is_success:
        try:
            delete_connector(client, connector_id)
            cleanup = f"deleted orphan connector id={connector_id}"
        except RuntimeError as exc:
            cleanup = f"orphan connector id={connector_id} remains ({exc})"
        raise RuntimeError(
            f"link failed for {payload['name']} ({cleanup}): {link_resp.text}"
        )

    cc_pair_id = link_resp.json()["data"]
    return connector_id, cc_pair_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Onyx Monday connectors for missing workspaces"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print workspaces that would be created without calling Onyx",
    )
    parser.add_argument(
        "--connectors-json",
        type=Path,
        default=DEFAULT_CONNECTORS_JSON,
        help="Path to connectors.json listing existing Onyx connectors",
    )
    parser.add_argument(
        "--workspaces-file",
        type=Path,
        default=None,
        help=(
            "JSON manifest of Monday workspaces [{id, name, kind, state}]. "
            f"Defaults to {DEFAULT_WORKSPACES_MANIFEST.name} when present."
        ),
    )
    parser.add_argument(
        "--fetch-from-monday",
        action="store_true",
        help="Live-fetch workspaces from Monday API instead of the manifest file",
    )
    parser.add_argument(
        "--write-manifest",
        type=Path,
        default=None,
        nargs="?",
        const=DEFAULT_WORKSPACES_MANIFEST,
        help=(
            "After fetching from Monday, write the workspace list to this JSON file "
            f"(default: {DEFAULT_WORKSPACES_MANIFEST.name})"
        ),
    )
    parser.add_argument(
        "--skip-templates",
        action="store_true",
        help="Skip Monday workspaces with kind=template",
    )
    parser.add_argument(
        "--skip-board-check",
        action="store_true",
        help=(
            "Do not pre-filter workspaces without accessible boards "
            "(sync connectors will fail validation on link)"
        ),
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.5,
        help="Pause between Onyx API calls to avoid rate limits",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.dry_run and not ONYX_PAT:
        print("ONYX_PAT is required (omit only with --dry-run)", file=sys.stderr)
        sys.exit(1)
    if not args.connectors_json.is_file():
        print(f"connectors.json not found: {args.connectors_json}", file=sys.stderr)
        sys.exit(1)

    existing_workspace_ids = load_existing_workspace_ids(args.connectors_json)
    taken_names = load_existing_connector_names(args.connectors_json)

    try:
        workspaces = resolve_workspaces(
            workspaces_file=args.workspaces_file,
            fetch_from_monday=args.fetch_from_monday,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if args.write_manifest is not None and args.fetch_from_monday:
        args.write_manifest.write_text(json.dumps(workspaces, indent=2) + "\n")
        print(f"Wrote {len(workspaces)} workspaces to {args.write_manifest}")

    workspace_ids_with_boards: set[str] | None = None
    if not args.skip_board_check:
        if not MONDAY_API_TOKEN:
            print(
                "MONDAY_API_TOKEN not set; skipping board pre-check "
                "(use --skip-board-check to silence this)",
                file=sys.stderr,
            )
        else:
            with httpx.Client(headers=monday_headers(), timeout=120) as monday_client:
                workspace_ids_with_boards = fetch_workspace_ids_with_boards(
                    monday_client
                )
            print(
                f"Workspaces with at least one board: {len(workspace_ids_with_boards)}"
            )

    to_create, skipped_no_boards = select_workspaces_to_create(
        workspaces,
        existing_workspace_ids,
        skip_templates=args.skip_templates,
        workspace_ids_with_boards=workspace_ids_with_boards,
    )

    print(f"Monday workspaces: {len(workspaces)}")
    print(f"Already in Onyx: {len(existing_workspace_ids)} workspace ids")
    print(f"Skipped (no accessible boards): {len(skipped_no_boards)}")
    print(f"To create: {len(to_create)} workspaces")

    if skipped_no_boards and (args.dry_run or len(to_create) == 0):
        for workspace in skipped_no_boards[:10]:
            print(
                f"  skip no boards: {workspace.get('name')} "
                f"(workspace_id={workspace.get('id')})"
            )
        if len(skipped_no_boards) > 10:
            print(f"  ... and {len(skipped_no_boards) - 10} more")

    if not to_create:
        print("Nothing to do.")
        return

    if args.dry_run:
        for workspace in to_create:
            workspace_id = str(workspace["id"])
            name = unique_connector_name(
                str(workspace.get("name") or ""),
                workspace_id,
                taken_names,
            )
            taken_names.add(name)
            print(
                f"  would create: name={name!r} workspace_id={workspace_id} "
                f"kind={workspace.get('kind')}"
            )
        return

    onyx_headers = {
        "Authorization": f"Bearer {ONYX_PAT}",
        "Content-Type": "application/json",
    }
    created: list[tuple[str, str, int, int]] = []
    failed: list[tuple[str, str]] = []

    with httpx.Client(headers=onyx_headers, timeout=120) as onyx_client:
        for workspace in to_create:
            workspace_id = str(workspace["id"])
            name = unique_connector_name(
                str(workspace.get("name") or ""),
                workspace_id,
                taken_names,
            )
            taken_names.add(name)
            payload = build_connector_payload(name, workspace_id)
            try:
                connector_id, cc_pair_id = create_and_link(
                    onyx_client, payload, CREDENTIAL_ID
                )
                created.append((name, workspace_id, connector_id, cc_pair_id))
                print(
                    f"OK {name}: workspace={workspace_id} "
                    f"connector={connector_id} cc_pair={cc_pair_id}"
                )
            except RuntimeError as exc:
                failed.append((name, str(exc)))
                print(f"FAIL {name}: {exc}", file=sys.stderr)
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

    print(f"\nCreated {len(created)}, failed {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
