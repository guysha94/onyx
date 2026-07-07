# Monday.com connector

Indexes boards, items, column values, updates, and file references from the
[Monday.com GraphQL API](https://developer.monday.com/api-reference/).

## Auth

Static API token via `credentials["monday_api_token"]` (sent as the `Authorization`
header value).

## Config kwargs

| Kwarg           | Type                | Description                                              |
| --------------- | ------------------- | -------------------------------------------------------- |
| `board_ids`     | `list[str] \| None` | Restrict indexing to specific board IDs                  |
| `workspace_ids` | `list[str] \| None` | Restrict indexing to boards in specific workspaces       |
| `batch_size`    | `int`               | Documents per yielded batch (default `INDEX_BATCH_SIZE`) |

## Pagination

- Boards: `boards(limit, page)` page-numbered loop (list query only — no nested items).
- Items: per board, `boards(ids: [...]) { items_page }` then `next_items_page(cursor)` until cursor is null.

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

## Local smoke test

```bash
export MONDAY_API_TOKEN=...
python -m onyx.connectors.monday.connector
```
