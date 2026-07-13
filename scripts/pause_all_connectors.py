"""Pause all Onyx connector-credential pairs (stops scheduled sync + in-flight indexing).

For each cc_pair that is currently syncing (ACTIVE, SCHEDULED, or INITIAL_INDEXING),
calls:
  PUT /manage/admin/cc-pair/{cc_pair_id}/status  {"status": "PAUSED"}

Pausing also cancels any in-progress indexing attempts for that cc_pair.

Usage:
  export ONYX_PAT="onyx_pat_..."
  uv run pause_all_connectors.py --dry-run   # preview only
  uv run pause_all_connectors.py             # pause all active connectors

"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any
from dotenv import load_dotenv
import httpx


load_dotenv()
BASE_URL = os.environ.get(
    "ONYX_API_URL", ""
)
ONYX_PAT = os.environ.get("ONYX_PAT", "")

ACTIVE_STATUSES = frozenset({"ACTIVE", "SCHEDULED", "INITIAL_INDEXING"})
SKIP_STATUSES = frozenset({"PAUSED", "DELETING", "INVALID"})


def fetch_indexing_statuses(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.post(
        f"{BASE_URL}/admin/connector/indexing-status",
        json={"get_all_connectors": True},
    )
    if not response.is_success:
        raise RuntimeError(
            f"failed to list connectors ({response.status_code}): {response.text}"
        )

    statuses: list[dict[str, Any]] = []
    for group in response.json():
        for connector in group.get("indexing_statuses") or []:
            statuses.append(connector)
    return statuses


def select_connectors_to_pause(
    statuses: list[dict[str, Any]],
    *,
    source: str | None,
    include_in_progress_only: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for connector in statuses:
        cc_pair_status = connector.get("cc_pair_status")
        if cc_pair_status in SKIP_STATUSES:
            continue
        if cc_pair_status not in ACTIVE_STATUSES:
            continue
        if include_in_progress_only and not connector.get("in_progress"):
            continue
        if source is not None:
            connector_source = (connector.get("source") or "").lower()
            if connector_source != source.lower():
                continue
        selected.append(connector)
    return sorted(
        selected, key=lambda c: (c.get("name") or "", c.get("cc_pair_id") or 0)
    )


def pause_cc_pair(client: httpx.Client, cc_pair_id: int) -> None:
    response = client.put(
        f"{BASE_URL}/admin/cc-pair/{cc_pair_id}/status",
        json={"status": "PAUSED"},
    )
    if not response.is_success:
        raise RuntimeError(f"pause failed for cc_pair={cc_pair_id}: {response.text}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pause all active Onyx connector syncs via admin API"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print connectors that would be paused without calling Onyx",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Only pause connectors for this source (e.g. github, jira)",
    )
    parser.add_argument(
        "--in-progress-only",
        action="store_true",
        help="Only pause connectors with an indexing job currently in progress",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.25,
        help="Pause between Onyx API calls to avoid rate limits",
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
            all_statuses = fetch_indexing_statuses(client)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        to_pause = select_connectors_to_pause(
            all_statuses,
            source=args.source,
            include_in_progress_only=args.in_progress_only,
        )

        print(f"Total cc_pairs: {len(all_statuses)}")
        print(f"To pause: {len(to_pause)}")

        if not to_pause:
            print("Nothing to do.")
            return

        if args.dry_run:
            for connector in to_pause:
                in_progress = "in_progress" if connector.get("in_progress") else "idle"
                print(
                    f"  would pause: cc_pair={connector.get('cc_pair_id')} "
                    f"name={connector.get('name')} "
                    f"source={connector.get('source')} "
                    f"status={connector.get('cc_pair_status')} "
                    f"{in_progress}"
                )
            return

        paused = 0
        failed: list[tuple[int, str]] = []

        for connector in to_pause:
            cc_pair_id = int(connector["cc_pair_id"])
            name = connector.get("name", "?")
            try:
                pause_cc_pair(client, cc_pair_id)
                paused += 1
                print(f"OK paused cc_pair={cc_pair_id} ({name})")
            except RuntimeError as exc:
                failed.append((cc_pair_id, str(exc)))
                print(f"FAIL cc_pair={cc_pair_id} ({name}): {exc}", file=sys.stderr)
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

        print(f"\nPaused {paused}, failed {len(failed)}")
        if failed:
            sys.exit(1)


if __name__ == "__main__":
    main()
