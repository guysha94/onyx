# Monday.com connector

Indexes items (with column values, updates, and file references) from the
[Monday.com GraphQL API](https://developer.monday.com/api-reference/) via the
[`monday`](https://pypi.org/project/monday/) Python SDK.

## Auth

Static API token via `credentials["monday_api_token"]` (sent as the `Authorization`
header value).

Required API scopes for **Auto Sync Permissions**:

- `boards:read`
- `workspaces:read`
- `users:read` (required for subscriber emails in ACL)
- `teams:read`

## Config kwargs

| Kwarg           | Type                | Description                                              |
| --------------- | ------------------- | -------------------------------------------------------- |
| `board_ids`     | `list[str] \| None` | Restrict indexing to specific board IDs                  |
| `workspace_ids` | `list[str] \| None` | Restrict indexing to boards in specific workspaces       |
| `batch_size`    | `int`               | Documents per yielded batch (default `INDEX_BATCH_SIZE`) |

## Traversal

Workspace-first discovery:

1. Resolve workspace ids (all, filtered, or from board payloads).
2. For each workspace, page boards via `_LIST_BOARDS_QUERY` (includes
   `workspace { id name }` — required for closed workspaces missing from
   `get_workspaces`).
3. For each board, fetch items via custom GraphQL (`items_page` + `next_items_page`).

When only `board_ids` is set, boards are fetched directly with workspace metadata.

## Searchable hierarchy metadata

Every indexed item document carries workspace/board context in:

- `Document.metadata` — `workspace_id`, `workspace_name`, `board_id`, `board_name`
  (participates in hybrid search via metadata suffix + Tag filters).
- Section text — `Workspace: …` / `Board: …` context blurb (Miro pattern).
- `doc_metadata.hierarchy` — Postgres breadcrumbs only (not searchable).

Opaque IDs (`workspace_id`, `board_id`) are excluded from the embedding metadata suffix
but remain in `metadata` for exact Tag lookup (see EE search fast path).

Uses Monday.com API version `2025-10` (see `API-Version` header).

## Incremental sync

`poll_source` passes a time window; items outside `updated_at` are skipped
client-side.

## Permission sync (Enterprise Edition)

When **Document Access** is set to **Auto Sync Permissions** (`access_type=sync`):

- Board-level ACL is resolved from Monday.com (`board_kind`, `permissions`,
  owners, subscribers, **closed-workspace** membership) and applied to every indexed item.
- ACL is attached at **index time** (`Document.external_access`) and refreshed by
  slim perm sync (~30 minutes).
- Subscriber lists are paginated (100/page) so large teams are not truncated.
- `validate_perm_sync` fails if the probe board resolves to an empty private ACL.

Items inherit their parent board's permissions (same pattern as Jira project ACLs).

ACL GraphQL is executed via `MondayApiClient.run_query` in
`ee/onyx/external_permissions/monday/page_access.py`.

## Deploy / reindex

After connector or ACL changes:

1. Restart Celery workers.
2. Confirm API token scopes (especially `users:read`).
3. Run permission sync and re-index the Monday connector.
4. Verify OpenSearch hits have non-empty `access_control_list` (or `public: true`)
   and `workspace_name` reflects the real workspace (e.g. `AI R&D`).

## Local smoke test

```bash
export MONDAY_API_TOKEN=...
python -m onyx.connectors.monday.connector
```
