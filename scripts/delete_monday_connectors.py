"""Delete all Onyx Monday.com connector-credential pairs.

For each Monday cc_pair:
  1. Pause if currently syncing (ACTIVE, SCHEDULED, or INITIAL_INDEXING)
  2. POST /manage/admin/deletion-attempt  {"connector_id": ..., "credential_id": ...}

Deletion runs asynchronously via the connector-deletion Celery worker. Use
--wait to block until Monday cc_pairs disappear from indexing status.

Usage:
  export ONYX_PAT="onyx_pat_..."
  uv run delete_monday_connectors.py --dry-run
  uv run delete_monday_connectors.py
  uv run delete_monday_connectors.py --wait

Optional env vars:
  ONYX_API_URL  default: https://sp-ai-platform.superplay.dev/api/manage
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("ONYX_API_URL", "")
ONYX_PAT = os.environ.get("ONYX_PAT", "")

MONDAY_SOURCE = "monday"
ACTIVE_STATUSES = frozenset({"ACTIVE", "SCHEDULED", "INITIAL_INDEXING"})
SKIP_STATUSES = frozenset({"DELETING"})


def fetch_connector_statuses(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(f"{BASE_URL}/admin/connector/status")
    if not response.is_success:
        raise RuntimeError(
            f"failed to list connector statuses ({response.status_code}): {response.text}"
        )
    return response.json()


def fetch_indexing_statuses(client: httpx.Client) -> dict[int, dict[str, Any]]:
    response = client.post(
        f"{BASE_URL}/admin/connector/indexing-status",
        json={"get_all_connectors": True},
    )
    if not response.is_success:
        raise RuntimeError(
            f"failed to list indexing statuses ({response.status_code}): {response.text}"
        )

    by_cc_pair_id: dict[int, dict[str, Any]] = {}
    for group in response.json():
        for connector in group.get("indexing_statuses") or []:
            cc_pair_id = connector.get("cc_pair_id")
            if cc_pair_id is not None:
                by_cc_pair_id[int(cc_pair_id)] = connector
    return by_cc_pair_id


def select_monday_connectors(
    connector_statuses: list[dict[str, Any]],
    indexing_by_cc_pair_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for connector_status in connector_statuses:
        connector = connector_status.get("connector") or {}
        if (connector.get("source") or "").lower() != MONDAY_SOURCE:
            continue

        cc_pair_id = int(connector_status["cc_pair_id"])
        credential = connector_status.get("credential") or {}
        indexing = indexing_by_cc_pair_id.get(cc_pair_id) or {}
        cc_pair_status = indexing.get("cc_pair_status")

        if cc_pair_status in SKIP_STATUSES:
            continue

        selected.append(
            {
                "cc_pair_id": cc_pair_id,
                "name": connector_status.get("name") or connector.get("name") or "?",
                "connector_id": int(connector["id"]),
                "credential_id": int(credential["id"]),
                "cc_pair_status": cc_pair_status,
                "docs_indexed": indexing.get("docs_indexed", 0),
            }
        )

    return sorted(selected, key=lambda c: (c["name"], c["cc_pair_id"]))


def pause_cc_pair(client: httpx.Client, cc_pair_id: int) -> None:
    response = client.put(
        f"{BASE_URL}/admin/cc-pair/{cc_pair_id}/status",
        json={"status": "PAUSED"},
    )
    if not response.is_success:
        raise RuntimeError(f"pause failed for cc_pair={cc_pair_id}: {response.text}")


def delete_cc_pair(client: httpx.Client, connector_id: int, credential_id: int) -> None:
    response = client.post(
        f"{BASE_URL}/admin/deletion-attempt",
        json={"connector_id": connector_id, "credential_id": credential_id},
    )
    if not response.is_success:
        raise RuntimeError(
            f"deletion attempt failed for connector={connector_id} "
            f"credential={credential_id}: {response.text}"
        )


def wait_for_monday_deletions(
    client: httpx.Client,
    cc_pair_ids: set[int],
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        connector_statuses = fetch_connector_statuses(client)
        indexing_by_cc_pair_id = fetch_indexing_statuses(client)

        remaining: list[dict[str, Any]] = []
        for connector_status in connector_statuses:
            cc_pair_id = int(connector_status["cc_pair_id"])
            if cc_pair_id not in cc_pair_ids:
                continue
            connector = connector_status.get("connector") or {}
            if (connector.get("source") or "").lower() != MONDAY_SOURCE:
                continue
            remaining.append(
                {
                    "cc_pair_id": cc_pair_id,
                    "name": connector_status.get("name", "?"),
                    "cc_pair_status": (
                        indexing_by_cc_pair_id.get(cc_pair_id) or {}
                    ).get("cc_pair_status"),
                }
            )

        if not remaining:
            print("All targeted Monday cc_pairs are gone.")
            return

        print(f"Waiting for {len(remaining)} Monday cc_pair(s) to finish deleting...")
        for connector in remaining[:5]:
            print(
                f"  cc_pair={connector['cc_pair_id']} "
                f"name={connector['name']} "
                f"status={connector['cc_pair_status']}"
            )
        if len(remaining) > 5:
            print(f"  ... and {len(remaining) - 5} more")
        time.sleep(poll_seconds)

    raise TimeoutError(
        f"Timed out after {timeout_seconds}s waiting for Monday cc_pair deletion"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete all Monday.com Onyx connector-credential pairs via admin API"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Monday connectors that would be deleted without calling Onyx",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Block until targeted Monday cc_pairs disappear from indexing status",
    )
    parser.add_argument(
        "--wait-timeout-seconds",
        type=float,
        default=1800.0,
        help="Max seconds to wait with --wait (default: 1800)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Polling interval for --wait (default: 5)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.25,
        help="Pause between Onyx API calls to avoid rate limits",
    )
    parser.add_argument(
        "--skip-pause",
        action="store_true",
        help="Skip pausing active connectors before submitting deletion attempts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.dry_run and not ONYX_PAT:
        print("ONYX_PAT is required (omit only with --dry-run)", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {ONYX_PAT}",
        "Content-Type": "application/json",
    }

    with httpx.Client(headers=headers, timeout=120) as client:
        try:
            connector_statuses = fetch_connector_statuses(client)
            indexing_by_cc_pair_id = fetch_indexing_statuses(client)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        to_delete = select_monday_connectors(
            connector_statuses,
            indexing_by_cc_pair_id,
        )

        monday_total = sum(
            1
            for connector_status in connector_statuses
            if (connector_status.get("connector") or {}).get("source") == MONDAY_SOURCE
        )
        deleting = monday_total - len(to_delete)

        print(f"Monday cc_pairs total: {monday_total}")
        if deleting:
            print(f"Already deleting (skipped): {deleting}")
        print(f"To delete: {len(to_delete)}")

        if not to_delete:
            print("Nothing to do.")
            return

        if args.dry_run:
            for connector in to_delete:
                print(
                    f"  would delete: cc_pair={connector['cc_pair_id']} "
                    f"name={connector['name']} "
                    f"connector={connector['connector_id']} "
                    f"credential={connector['credential_id']} "
                    f"status={connector['cc_pair_status']} "
                    f"docs_indexed={connector['docs_indexed']}"
                )
            return

        deleted = 0
        failed: list[tuple[int, str]] = []

        for connector in to_delete:
            cc_pair_id = connector["cc_pair_id"]
            name = connector["name"]
            try:
                if (
                    not args.skip_pause
                    and connector["cc_pair_status"] in ACTIVE_STATUSES
                ):
                    pause_cc_pair(client, cc_pair_id)
                delete_cc_pair(
                    client,
                    connector_id=connector["connector_id"],
                    credential_id=connector["credential_id"],
                )
                deleted += 1
                print(
                    f"OK queued deletion cc_pair={cc_pair_id} ({name}) "
                    f"connector={connector['connector_id']}"
                )
            except RuntimeError as exc:
                failed.append((cc_pair_id, str(exc)))
                print(f"FAIL cc_pair={cc_pair_id} ({name}): {exc}", file=sys.stderr)
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

        print(f"\nQueued deletion for {deleted}, failed {len(failed)}")

        if failed:
            sys.exit(1)

        if args.wait:
            try:
                wait_for_monday_deletions(
                    client,
                    {connector["cc_pair_id"] for connector in to_delete},
                    timeout_seconds=args.wait_timeout_seconds,
                    poll_seconds=args.poll_seconds,
                )
            except TimeoutError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
