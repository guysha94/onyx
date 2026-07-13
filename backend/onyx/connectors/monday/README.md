# Monday.com connector

Indexes items (with column values, updates, and file references) from the
[Monday.com GraphQL API](https://developer.monday.com/api-reference/) via the
[`monday`](https://pypi.org/project/monday/) Python SDK.

## Auth

Static API token via `credentials["monday_api_token"]` (sent as the `Authorization`
header value).

## Config kwargs

| Kwarg           | Type                | Description                                              |
| --------------- | ------------------- | -------------------------------------------------------- |
| `board_ids`     | `list[str] \| None` | Restrict indexing to specific board IDs                  |
| `workspace_ids` | `list[str] \| None` | Restrict indexing to boards in specific workspaces       |
| `batch_size`    | `int`               | Documents per yielded batch (default `INDEX_BATCH_SIZE`) |

## Traversal

Workspace-first discovery (like `tmp.py`):

1. List workspaces (`workspaces.get_workspaces`) — all, or filtered by `workspace_ids`.
2. For each workspace, page boards (`boards.fetch_boards` with `workspace_ids`).
3. For each board, fetch items via custom GraphQL (`items_page` + `next_items_page`) so
   full item fields (URL, updates, assets, creator) are available.

When only `board_ids` is set, boards are fetched directly (with workspace metadata from
the API) without iterating workspaces.

## Searchable hierarchy metadata

Every indexed item document carries workspace/board context in:

- `Document.metadata` — `workspace_id`, `workspace_name`, `board_id`, `board_name`
  (participates in hybrid search via metadata suffix + Tag filters).
- Section text — `Workspace: …` / `Board: …` context blurb (Miro pattern).
- `doc_metadata.hierarchy` — Postgres breadcrumbs only (not searchable).

Opaque IDs (`workspace_id`, `board_id`) are excluded from the embedding metadata suffix
but remain in `metadata` for exact Tag lookup.

Uses Monday.com API version `2025-10` (see `API-Version` header).

## Incremental sync

`poll_source` passes a time window; items outside `updated_at` are skipped
client-side.

## Permission sync (Enterprise Edition)

When **Document Access** is set to **Auto Sync Permissions** (`access_type=sync`):

- Board-level ACL is resolved from Monday.com (`board_kind`, `permissions`,
  owners, subscribers, workspace membership) and applied to every indexed item.
- Team members on a board are expanded to user emails during sync.
- A background doc-permission sync runs every ~30 minutes to refresh ACLs.

Items inherit their parent board's permissions (same pattern as Jira project ACLs).

Custom GraphQL for ACL (`_BOARD_ACCESS_QUERY`) is executed via `MondayApiClient.run_query`.

## Local smoke test

```bash
export MONDAY_API_TOKEN=...
python -m onyx.connectors.monday.connector
```
