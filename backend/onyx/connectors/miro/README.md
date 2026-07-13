# Miro Connector (FORK: miro)

Indexes visual asset boards from Miro - images, screenshots, UI mockups, icons -
for semantic search. This is a fork-only connector (not part of upstream Onyx),
tracked similarly to the Monday.com connector (Jira AI-69).

## What gets indexed

Onyx does not do multimodal embeddings: each image is captioned by a vision LLM
at index time, and that caption text is what gets embedded and searched. This
connector's job is therefore to (1) get the image bytes into Onyx's file store,
and (2) attach the best possible text context around each asset so the caption
+ search are as useful as possible.

- **One Onyx `Document` per Miro image item.** Miro boards are not file
  hierarchies, so unlike a file-centric connector (e.g. Google Drive), each
  visual asset is indexed individually rather than as an attachment of a
  parent file.
- **Board + frame context, not coordinates.** Onyx has no field for
  bounding boxes or x/y position, and dense unlabeled asset grids make
  per-asset proximity matching noisy and unreliable. Instead, each asset
  inherits:
  - its board's name,
  - its containing frame's title (via the Miro API's `parent.id` field - no
    coordinate math), and
  - any free text from sticky notes/text/shapes that share that frame.
  This context is folded into (a) the image's display name (which flows into
  the vision-caption prompt), (b) a synthesized text section, and (c)
  document metadata (indexed as a searchable suffix).
- **Non-captionable images (svg/gif) become text-only documents** carrying
  the same board/frame context, since Onyx's image pipeline only captions
  png/jpg/webp.
- **Text/sticky-note/shape items are not indexed as their own documents** in
  this MVP - they're only consumed as frame-level context for the images
  around them.
- **Videos are out of scope** - these boards don't contain any.

## Search-result thumbnails

In addition to semantic retrieval, matched assets render as an image
thumbnail in search/citation result cards (not just a text blurb + link).
This connector deliberately sets `Document.file_id` equal to the stored
image's file-store id (`image_file_id`). The existing
`GET /api/chat/file/{file_id}` ACL check grants access whenever
`Document.file_id == file_id`, so this "auth trick" lets thumbnails work with
no backend auth changes. See the frontend/backend "FORK: miro" thumbnail
wiring in `backend/onyx/context/search/models.py`,
`backend/ee/onyx/server/query_and_chat/models.py`, and the search/citation
result card components under `web/src/`.

## Auth

Static Miro access token (MVP only - full OAuth 2.0 authorization-code flow
is a future enhancement). Credential JSON:

```json
{
  "miro_access_token": "<token>"
}
```

Sent as `Authorization: Bearer <token>` on every Miro REST API call.

## Config

- `board_ids: list[str] | None` - restrict indexing to specific boards. If
  omitted, all boards visible to the token are indexed (optionally scoped by
  `team_id`).
- `team_id: str | None` - restrict full-board enumeration to a specific team.
  Ignored when `board_ids` is set.
- `batch_size: int` - documents yielded per batch (defaults to
  `INDEX_BATCH_SIZE`).

## API usage

- `GET /v2/boards` - board enumeration (`limit`/`offset` pagination, optional
  `team_id`).
- `GET /v2/boards/{board_id}` - single-board lookup when `board_ids` is set.
- `GET /v2/boards/{board_id}/items` - item enumeration (`cursor`/`limit`
  pagination). Fetched without a `type` filter so images, frames, and
  text/sticky/shape items can be cross-referenced in one pass via `parent.id`.
- Image bytes: each image item's `data.imageUrl`, with `format=original` and
  `redirect=true` forced onto the query string.

Only `id`, `type`, `data`, `parent`, and `modifiedAt` are used from each item.
`position`/`geometry` (coordinates, bounding box) are intentionally unused.

## Auto Sync Permissions (EE)

The connector supports "Auto Sync Permissions" (`access_type = "sync"`) via a
team-based external-group model:

- `retrieve_all_slim_docs_perm_sync` tags each asset with its board's
  `ExternalAccess` derived from the board `sharingPolicy`:
  - org-wide / public-link access -> public,
  - team access -> the board's team external group (`miro_team_<team_id>`),
  - private-to-individuals -> admin-only (fail closed).
- `ee/onyx/external_permissions/miro/group_sync.py` resolves each Miro team to
  its member emails (`GET /v2/orgs/{org_id}/teams` + `/members`, joined against
  `GET /v2/orgs/{org_id}/members` for emails) and yields one
  `ExternalUserGroup` per team.

**Requires an organization-scoped access token** (scopes incl.
`organizations:read`, `organizations:teams:read`). Indexing itself works with a
board-only token, but enabling perm sync validates org-member access up front
and fails fast otherwise. Board-member-level (per-individual) shares are not
resolvable to emails (different id space with no email endpoint), so such
boards fall back to their team group or admin-only.

## Out of scope (future work)

- Full OAuth 2.0 authorization-code flow.
- Per-individual board-member ACLs (only team-level access is resolved to emails).
- Checkpointed indexing for very large board sets.
- Generalizing the thumbnail auth trick to embedded/attachment-style images
  where `image_file_id != Document.file_id`.
