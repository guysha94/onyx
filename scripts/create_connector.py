"""Bulk-create Onyx GitHub connectors for superplay-co repositories.

Creates one connector + cc_pair per non-empty repo that is not already
indexed (see connectors.json). Skips archived repos and repos with size 0.

Steps per repo:
  1. POST /manage/admin/connector
  2. PUT  /manage/connector/{id}/credential/{credential_id}

Usage:
  export ONYX_PAT="onyx_pat_..."
  uv run create_connector.py --dry-run          # preview (uses repos manifest)
  uv run create_connector.py                    # create missing connectors

Repo list sources (first match wins):
  1. --repos-file path.json
  2. superplay-co-repos.json next to this script (refresh via GitHub MCP/API)
  3. Live GitHub API (requires GITHUB_TOKEN/GH_TOKEN with org repo read)

Optional env vars:
  ONYX_API_URL        default: https://sp-ai-platform.superplay.dev/api/manage
  ONYX_CREDENTIAL_ID  default: 20 (GitHub credential)
  GITHUB_ORG          default: superplay-co
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
import httpx

load_dotenv()
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONNECTORS_JSON = SCRIPT_DIR / "connectors.json"
DEFAULT_REPOS_MANIFEST = SCRIPT_DIR / "superplay-co-repos.json"

BASE_URL = os.environ.get(
    "ONYX_API_URL", ""
)
ONYX_PAT = os.environ.get(
    "ONYX_PAT",
    "",
)
CREDENTIAL_ID = int(os.environ.get("ONYX_CREDENTIAL_ID", "20"))
GITHUB_ORG = os.environ.get("GITHUB_ORG", "superplay-co")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")

REPO_OWNER = GITHUB_ORG
REFRESH_FREQ = 1800
PRUNE_FREQ = 604800


def load_existing_repos(connectors_path: Path) -> set[str]:
    data: list[dict[str, Any]] = json.loads(connectors_path.read_text())
    existing: set[str] = set()
    for connector in data:
        if connector.get("source") != "github":
            continue
        cfg = connector.get("connector_specific_config") or {}
        repos_field = cfg.get("repositories", "")
        for name in str(repos_field).split(","):
            name = name.strip()
            if name:
                existing.add(name)
        name = connector.get("name", "")
        if name and name != "github":
            existing.add(name)
    return existing


def load_repos_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def fetch_org_repos(client: httpx.Client, org: str) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.get(
            f"https://api.github.com/orgs/{org}/repos",
            params={"per_page": 100, "page": page, "type": "all"},
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos


def resolve_org_repos(
    *,
    repos_file: Path | None,
    fetch_from_github: bool,
) -> list[dict[str, Any]]:
    if repos_file is not None:
        if not repos_file.is_file():
            raise FileNotFoundError(f"repos manifest not found: {repos_file}")
        return load_repos_manifest(repos_file)

    if not fetch_from_github and DEFAULT_REPOS_MANIFEST.is_file():
        return load_repos_manifest(DEFAULT_REPOS_MANIFEST)

    if not GITHUB_TOKEN:
        raise RuntimeError(
            "GITHUB_TOKEN or GH_TOKEN is required for live GitHub fetch "
            f"(or provide --repos-file / {DEFAULT_REPOS_MANIFEST.name})"
        )

    github_headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(headers=github_headers, timeout=120) as github_client:
        return fetch_org_repos(github_client, GITHUB_ORG)


def select_repos_to_create(
    org_repos: list[dict[str, Any]], existing: set[str]
) -> list[str]:
    candidates: list[str] = []
    for repo in org_repos:
        name = repo["name"]
        if repo.get("archived"):
            continue
        if repo.get("size", 0) <= 0:
            continue
        if name in existing:
            continue
        candidates.append(name)
    return sorted(candidates, key=str.lower)


def build_connector_payload(repo_name: str) -> dict[str, Any]:
    return {
        "name": repo_name,
        "access_type": "sync",
        "source": "github",
        "input_type": "poll",
        "refresh_freq": REFRESH_FREQ,
        "prune_freq": PRUNE_FREQ,
        "indexing_start": None,
        "connector_specific_config": {
            "repo_owner": REPO_OWNER,
            "include_prs": True,
            "repositories": repo_name,
            "include_files": True,
            "include_issues": True,
        },
    }


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
        raise RuntimeError(
            f"link failed for {payload['name']} (orphan connector id={connector_id}): "
            f"{link_resp.text}"
        )

    cc_pair_id = link_resp.json()["data"]
    return connector_id, cc_pair_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Onyx GitHub connectors for missing superplay-co repos"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print repos that would be created without calling Onyx",
    )
    parser.add_argument(
        "--connectors-json",
        type=Path,
        default=DEFAULT_CONNECTORS_JSON,
        help="Path to connectors.json listing existing Onyx connectors",
    )
    parser.add_argument(
        "--repos-file",
        type=Path,
        default=None,
        help=(
            "JSON manifest of org repos [{name, size, archived}]. "
            f"Defaults to {DEFAULT_REPOS_MANIFEST.name} when present."
        ),
    )
    parser.add_argument(
        "--fetch-from-github",
        action="store_true",
        help="Live-fetch repos from GitHub API instead of the manifest file",
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

    existing = load_existing_repos(args.connectors_json)

    try:
        org_repos = resolve_org_repos(
            repos_file=args.repos_file,
            fetch_from_github=args.fetch_from_github,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    to_create = select_repos_to_create(org_repos, existing)
    print(f"GitHub org {GITHUB_ORG}: {len(org_repos)} repos")
    print(f"Already in Onyx: {len(existing)} repo names")
    print(f"To create: {len(to_create)} non-empty repos")

    if not to_create:
        print("Nothing to do.")
        return

    if args.dry_run:
        for name in to_create:
            print(f"  would create: {name}")
        return

    onyx_headers = {
        "Authorization": f"Bearer {ONYX_PAT}",
        "Content-Type": "application/json",
    }
    created: list[tuple[str, int, int]] = []
    failed: list[tuple[str, str]] = []

    with httpx.Client(headers=onyx_headers, timeout=120) as onyx_client:
        for repo_name in to_create:
            payload = build_connector_payload(repo_name)
            try:
                connector_id, cc_pair_id = create_and_link(
                    onyx_client, payload, CREDENTIAL_ID
                )
                created.append((repo_name, connector_id, cc_pair_id))
                print(f"OK {repo_name}: connector={connector_id} cc_pair={cc_pair_id}")
            except RuntimeError as exc:
                failed.append((repo_name, str(exc)))
                print(f"FAIL {repo_name}: {exc}", file=sys.stderr)
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

    print(f"\nCreated {len(created)}, failed {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
