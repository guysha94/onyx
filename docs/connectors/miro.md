# Setting up the Miro connector

The Miro connector indexes the **image assets** on your Miro boards — screenshots, UI mockups,
icons, and other visual assets — so they're findable by semantic search, including in their
board/frame context. Matching assets render as an image thumbnail directly in search and chat
citation results.

## What gets indexed

- **One document per image item** on every board the access token can access. Miro boards are not
  file hierarchies, so each visual asset is indexed on its own rather than as an attachment of a
  parent document.
- Each asset is captioned by Onyx's vision LLM at index time, and that caption — plus the asset's
  **board name**, **frame title** (if it's inside a frame), and any nearby sticky-note/text/shape
  content in that frame — is what makes it searchable. There's no coordinate/bounding-box search;
  board/frame membership is the only structural signal used.
- Non-captionable image types (SVG, GIF) are indexed as text-only documents carrying the same
  board/frame context, since Onyx only captions PNG/JPG/WEBP.
- Text/sticky-note/shape items themselves are **not** indexed as separate documents — they're only
  used as frame-level context for the images around them.
- **Videos are not indexed** (out of scope).

Each result links straight back to the asset's position on the board.

## Before you start

You need a Miro **access token** (personal access token, or app token with board-read scope).
Onyx indexes exactly what that token can see.

### Get your access token

1. In Miro, go to your **profile → Settings → Apps**, or create a Miro app under
   **Miro Developer Platform** and generate an access token with `boards:read` scope.
2. Copy the token.

> Treat the token like a password — anyone with it can read the same boards.

## Connect Miro in Onyx

1. Go to **Admin → Connectors → Add Connector** and choose **Miro**.
2. Paste your **Access Token** when prompted for credentials.
3. (Optional) Restrict what gets indexed:
   - **Board IDs** — index only specific boards.
   - **Team ID** — index only boards owned by a specific team (ignored if Board IDs are set).
   - Leave both empty to index every board the token can access.
4. Save. Onyx will start an initial index and then keep it up to date automatically (incremental
   sync picks up items changed since the last run).

### Finding board / team IDs

Open a board in Miro and look at the URL: `https://miro.com/app/board/uXjVNAc1z9g=/` → the board
ID is `uXjVNAc1z9g=`. Team IDs appear in the URL when you open a team's boards list.

## Search result thumbnails

Search and chat citation cards show the actual asset image (not just a text blurb) whenever a
matched chunk has an associated image. This works out of the box with no extra setup.

## Permissions

In this version, everything the access token can access is indexed and visible to Onyx users who
can see the connector's documents. Per-board access control mapping (mirroring Miro board
permissions into Onyx) is planned as a follow-up.

## Troubleshooting

- **"Invalid Miro access token"** — the token is wrong or was revoked. Regenerate it and update
  the credential.
- **Some boards are missing** — the token's user doesn't have access to them, or they were
  excluded by the Board ID / Team ID filters. Use a token with broader access or clear the filters.
- **An asset shows as text-only, no thumbnail** — the original image was an SVG/GIF, which Onyx
  doesn't caption or thumbnail; the asset is still searchable by its board/frame context.
