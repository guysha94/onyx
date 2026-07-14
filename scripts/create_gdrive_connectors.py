"""Bulk-create Onyx Google Drive connectors — one cc_pair per Shared drive.

Matches the per-drive pattern in connectors.json (e.g. ai-innovation): each connector
scopes to a single Shared drive via shared_drive_urls.

Steps per Shared drive:
  1. POST /manage/admin/connector
  2. PUT  /manage/connector/{id}/credential/{credential_id}

Usage:
  export ONYX_PAT="onyx_pat_..."
  export GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE="/path/to/sa.json"
  export GOOGLE_PRIMARY_ADMIN="admin@company.com"
  uv run create_gdrive_connectors.py --dry-run
  uv run create_gdrive_connectors.py

Drive list sources (first match wins):
  1. --drives-file path.json
  2. gdrive-shared-drives.json next to this script
  3. Live Google Drive API (requires service account env vars)

Optional env vars:
  ONYX_API_URL                 default: https://sp-ai-platform.superplay.dev/api/manage
  ONYX_CREDENTIAL_ID           default: 23 (Google Drive credential)
  GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE  path to service-account JSON
  GOOGLE_APPLICATION_CREDENTIALS     fallback for service-account JSON path
  GOOGLE_PRIMARY_ADMIN           Workspace user to impersonate (domain-wide delegation)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


load_dotenv(override=True)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONNECTORS_JSON = SCRIPT_DIR / "connectors.json"
DEFAULT_DRIVES_MANIFEST = SCRIPT_DIR / "gdrive-shared-drives.json"

BASE_URL = os.environ.get("ONYX_API_URL", "")
ONYX_PAT = os.environ.get("ONYX_PAT", "")
CREDENTIAL_ID = int(os.environ.get("ONYX_CREDENTIAL_ID", "23"))
GOOGLE_PRIMARY_ADMIN = os.environ.get("GOOGLE_PRIMARY_ADMIN", "")
SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE"
) or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

REFRESH_FREQ = 1800
PRUNE_FREQ = 604800

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

FOLDER_URL_TEMPLATE = "https://drive.google.com/drive/folders/{drive_id}"

print(SERVICE_ACCOUNT_FILE)


def extract_id_from_url(url_or_id: str) -> str:
    value = url_or_id.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return urlparse(value).path.strip("/").split("/")[-1]
    return value


def load_existing_drive_ids(connectors_path: Path) -> set[str]:
    data: list[dict[str, Any]] = json.loads(connectors_path.read_text())
    existing: set[str] = set()
    for connector in data:
        if connector.get("source") != "google_drive":
            continue
        cfg = connector.get("connector_specific_config") or {}
        for field in ("shared_drive_urls", "shared_folder_urls"):
            raw = cfg.get(field)
            if not raw:
                continue
            for part in str(raw).split(","):
                drive_id = extract_id_from_url(part)
                if drive_id:
                    existing.add(drive_id)
    return existing


def load_existing_connector_names(connectors_path: Path) -> set[str]:
    data: list[dict[str, Any]] = json.loads(connectors_path.read_text())
    names: set[str] = set()
    for connector in data:
        if connector.get("source") != "google_drive":
            continue
        name = str(connector.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def load_drives_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def build_drive_service():
    if not SERVICE_ACCOUNT_FILE:
        raise RuntimeError(
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE or GOOGLE_APPLICATION_CREDENTIALS is required"
        )
    if not GOOGLE_PRIMARY_ADMIN:
        raise RuntimeError("GOOGLE_PRIMARY_ADMIN is required")

    sa_path = Path(SERVICE_ACCOUNT_FILE)
    if not sa_path.is_file():
        raise FileNotFoundError(f"service account file not found: {sa_path}")

    creds = service_account.Credentials.from_service_account_file(
        str(sa_path),
        scopes=DRIVE_SCOPES,
    ).with_subject(GOOGLE_PRIMARY_ADMIN)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def fetch_shared_drives(service: Any) -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        response = (
            service.drives()
            .list(
                pageSize=100,
                pageToken=page_token,
                fields="nextPageToken,drives(id,name)",
                useDomainAdminAccess=True,
            )
            .execute()
        )
        batch = response.get("drives") or []
        drives.extend(batch)
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return drives


def resolve_shared_drives(
    *,
    drives_file: Path | None,
    fetch_from_google: bool,
) -> list[dict[str, Any]]:
    if drives_file is not None:
        if not drives_file.is_file():
            raise FileNotFoundError(f"drives manifest not found: {drives_file}")
        return load_drives_manifest(drives_file)

    if not fetch_from_google and DEFAULT_DRIVES_MANIFEST.is_file():
        return load_drives_manifest(DEFAULT_DRIVES_MANIFEST)

    service = build_drive_service()
    return fetch_shared_drives(service)


def unique_connector_name(drive_name: str, drive_id: str, taken_names: set[str]) -> str:
    base = drive_name.strip() or f"gdrive-{drive_id}"
    base = re.sub(r"\s+", " ", base)
    if base not in taken_names:
        return base

    suffix = f" ({drive_id})"
    candidate = f"{base}{suffix}"
    if candidate not in taken_names:
        return candidate

    counter = 2
    while True:
        candidate = f"{base}{suffix}-{counter}"
        if candidate not in taken_names:
            return candidate
        counter += 1


def select_drives_to_create(
    drives: list[dict[str, Any]],
    existing_drive_ids: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for drive in drives:
        drive_id = str(drive.get("id") or "").strip()
        if not drive_id:
            continue
        if drive_id in existing_drive_ids:
            continue
        selected.append(drive)

    def _sort_key(drive: dict[str, Any]) -> tuple[str, str]:
        return (str(drive.get("name") or "").lower(), str(drive.get("id") or ""))

    return sorted(selected, key=_sort_key)


def build_connector_payload(name: str, drive_id: str) -> dict[str, Any]:
    return {
        "name": name,
        "access_type": "sync",
        "source": "google_drive",
        "input_type": "poll",
        "refresh_freq": REFRESH_FREQ,
        "prune_freq": PRUNE_FREQ,
        "indexing_start": None,
        "connector_specific_config": {
            "include_my_drives": False,
            "shared_drive_urls": FOLDER_URL_TEMPLATE.format(drive_id=drive_id),
            "specific_user_emails": "",
            "include_shared_drives": False,
            "exclude_domain_link_only": False,
            "include_files_shared_with_me": False,
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
        description="Create Onyx Google Drive connectors for missing Shared drives"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Shared drives that would be created without calling Onyx",
    )
    parser.add_argument(
        "--connectors-json",
        type=Path,
        default=DEFAULT_CONNECTORS_JSON,
        help="Path to connectors.json listing existing Onyx connectors",
    )
    parser.add_argument(
        "--drives-file",
        type=Path,
        default=None,
        help=(
            "JSON manifest of Shared drives [{id, name}]. "
            f"Defaults to {DEFAULT_DRIVES_MANIFEST.name} when present."
        ),
    )
    parser.add_argument(
        "--fetch-from-google",
        action="store_true",
        help="Live-fetch Shared drives from Google API instead of the manifest file",
    )
    parser.add_argument(
        "--write-manifest",
        type=Path,
        default=None,
        nargs="?",
        const=DEFAULT_DRIVES_MANIFEST,
        help=(
            "After fetching from Google, write the drive list to this JSON file "
            f"(default: {DEFAULT_DRIVES_MANIFEST.name})"
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

    existing_drive_ids = load_existing_drive_ids(args.connectors_json)
    taken_names = load_existing_connector_names(args.connectors_json)

    try:
        drives = resolve_shared_drives(
            drives_file=args.drives_file,
            fetch_from_google=args.fetch_from_google,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if args.write_manifest is not None and args.fetch_from_google:
        args.write_manifest.write_text(json.dumps(drives, indent=2) + "\n")
        print(f"Wrote {len(drives)} Shared drives to {args.write_manifest}")

    to_create = select_drives_to_create(drives, existing_drive_ids)

    print(f"Google Shared drives: {len(drives)}")
    print(f"Already in Onyx: {len(existing_drive_ids)} drive ids")
    print(f"To create: {len(to_create)} Shared drives")

    if not to_create:
        print("Nothing to do.")
        return

    if args.dry_run:
        for drive in to_create:
            drive_id = str(drive["id"])
            name = unique_connector_name(
                str(drive.get("name") or ""),
                drive_id,
                taken_names,
            )
            taken_names.add(name)
            print(
                f"  would create: name={name!r} drive_id={drive_id} "
                f"url={FOLDER_URL_TEMPLATE.format(drive_id=drive_id)}"
            )
        return

    onyx_headers = {
        "Authorization": f"Bearer {ONYX_PAT}",
        "Content-Type": "application/json",
    }
    created: list[tuple[str, str, int, int]] = []
    failed: list[tuple[str, str]] = []

    with httpx.Client(headers=onyx_headers, timeout=120) as onyx_client:
        for drive in to_create:
            drive_id = str(drive["id"])
            name = unique_connector_name(
                str(drive.get("name") or ""),
                drive_id,
                taken_names,
            )
            taken_names.add(name)
            payload = build_connector_payload(name, drive_id)
            try:
                connector_id, cc_pair_id = create_and_link(
                    onyx_client, payload, CREDENTIAL_ID
                )
                created.append((name, drive_id, connector_id, cc_pair_id))
                print(
                    f"OK {name}: drive={drive_id} "
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
