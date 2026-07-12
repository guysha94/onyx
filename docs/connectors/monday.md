# Setting up the monday.com connector

The monday.com connector indexes your **boards, items, item updates, and file references** so you
can search them in Onyx and get answers that cite back to the original monday.com items.

## What gets indexed

- **Items** on every board the API token can access (each item becomes one searchable document).
- **Column values** (status, text, people, dates, etc.) shown on each item.
- **Updates** — the conversation/comment thread on each item.
- **Files** — attachment names and links are indexed so items are findable by file name. File
  _contents_ are not downloaded in this version.

Each result links straight back to the item in monday.com.

## Before you start

You need a monday.com **API token**. Onyx indexes exactly what that token can see, so use a token
from an account/user with access to the boards you want searchable.

### Get your API token

1. In monday.com, click your **avatar** (bottom-left) → **Developers**.
2. Open **My Access Tokens** → **Show** (or **Generate**).
3. Copy the token.

> Admins can instead use the account-level token under **Administration → Connections → API**.
> Treat the token like a password — anyone with it can read the same data.

## Connect monday.com in Onyx

1. Go to **Admin → Connectors → Add Connector** and choose **monday.com**.
2. Paste your **API Token** when prompted for credentials.
3. (Optional) Restrict what gets indexed:
   - **Board IDs** — index only specific boards.
   - **Workspace IDs** — index only boards in specific workspaces.
   - Leave both empty to index every board the token can access.
4. Save. Onyx will start an initial index and then keep it up to date automatically (incremental
   sync picks up items changed since the last run).

### Finding board / workspace IDs

Open a board in monday.com and look at the URL:
`https://<your-account>.monday.com/boards/1234567890` → the board ID is `1234567890`.
Workspace IDs appear in the URL when you open a workspace.

## Permissions

When **Document Access** is set to **Auto Sync Permissions**, Onyx periodically
syncs board-level ACL from Monday.com. A document is searchable only for users who
can access it in Monday (board owners/subscribers, workspace members for closed
workspaces, etc.).

Requires Enterprise Edition (`ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=true`) and
Business tier for the Auto Sync option in the admin UI.

In this version, everything the API token can access is indexed. Per-board access
control mapping mirrors Monday board permissions at the item level (items inherit
their board's ACL).

## Troubleshooting

- **"Invalid monday.com API token"** — the token is wrong or was revoked. Regenerate it under
  **Developers → My Access Tokens** and update the credential.
- **Some boards are missing** — the token's user doesn't have access to them, or they were excluded
  by the Board/Workspace ID filters. Use a token with broader access or clear the filters.
- **Indexing is slow / pauses** — monday.com enforces an API complexity rate limit; the connector
  automatically backs off and retries, so large accounts simply take longer on the first sync.
