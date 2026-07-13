# Fork changes

This repo is a **fork of [Onyx](https://github.com/onyx-dot-app/onyx)**. This file is the
authoritative list of everything we changed relative to upstream, so that pulling upstream updates
stays low-conflict and any lost change is easy to re-apply.

**Principle:** keep all fork logic in **net-new files**; touch shared upstream files as little as
possible; tag every unavoidable edit with a `FORK:` marker. To find every touchpoint in the tree:

```bash
grep -rn "FORK:" backend/onyx web/src web/lib --include='*.py' --include='*.ts' --include='*.tsx'
```

## Syncing upstream

1. Work on a branch; never commit fork changes straight to a branch that tracks upstream `main`.
2. Enable conflict-resolution memory once: `git config rerere.enabled true`.
3. Add the upstream remote once: `git remote add upstream https://github.com/onyx-dot-app/onyx.git`.
4. Prefer rebasing onto an upstream **release tag**, not raw `main`:
   `git fetch upstream --tags && git rebase <tag>`.
5. After the rebase, run the grep above + the checklist below to confirm every touchpoint survived,
   then run the verification steps in `.claude/plans/` for the affected feature.

---

## Net-new files (no conflict risk)

These exist only in the fork; upstream never touches them.

| Path                                                                    | Purpose                                                                        |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `backend/onyx/connectors/monday/__init__.py`                            | package marker                                                                 |
| `backend/onyx/connectors/monday/client.py`                              | `MondayApiClient` SDK adapter                                                  |
| `backend/onyx/connectors/monday/connector.py`                           | `MondayConnector` implementation                                               |
| `backend/onyx/connectors/monday/README.md`                              | developer docs                                                                 |
| `backend/onyx/connectors/fork_registry.py`                              | fork-only `FORK_CONNECTOR_CLASS_MAP`                                           |
| `backend/tests/daily/connectors/monday/`                                | daily connector test                                                           |
| `docs/connectors/monday.md`                                             | user-facing setup guide                                                        |
| `web/lib/opal/src/logos/monday.tsx`                                     | `SvgMonday` logo                                                               |
| `backend/onyx/connectors/miro/__init__.py`                              | package marker                                                                 |
| `backend/onyx/connectors/miro/connector.py`                             | `MiroConnector` implementation                                                 |
| `backend/onyx/connectors/miro/README.md`                                | developer docs                                                                 |
| `backend/tests/unit/onyx/connectors/miro/`                              | unit tests (frame-context + doc-conversion + type routing + filename recovery) |
| `docs/connectors/miro.md`                                               | user-facing setup guide                                                        |
| `web/lib/opal/src/logos/miro.tsx`                                       | `SvgMiro` logo                                                                 |
| `backend/tests/unit/onyx/db/test_user_file.py`                          | unit test for the `get_file_id_by_user_file_id` UUID guard                     |
| `backend/tests/unit/ee/onyx/search/test_process_search_query.py`        | unit test for `_detect_miro_identifier_tag`                                    |
| `backend/tests/unit/ee/onyx/search/test_monday_process_search_query.py` | unit test for `_detect_monday_identifier_tag`                                  |
| `deployment/docker_compose/docker-compose.gpu.yml`                      | NVIDIA GPU reservations for model servers (4090 / WSL2)                        |
| `FORK_CHANGES.md`                                                       | this manifest                                                                  |

## Shared-file edits (conflict-prone — re-apply after each sync)

Each row is one edit to an upstream-maintained file. All carry a `FORK:` marker except where noted.

### Monday.com connector (Jira ticket AI-69)

| File                                                                   | Edit                                                                                                                                | Status |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------ |
| `backend/onyx/configs/constants.py`                                    | `MONDAY = "monday"` at end of `DocumentSource` enum + entry in `DocumentSourceDescription`, both `# FORK: monday`                   | ✅     |
| `backend/onyx/connectors/registry.py`                                  | one `# === FORK ===` block after the `CONNECTOR_CLASS_MAP` literal that does `CONNECTOR_CLASS_MAP.update(FORK_CONNECTOR_CLASS_MAP)` | ✅     |
| `web/src/lib/types.ts`                                                 | `Monday = "monday"  // FORK: monday` at end of `ValidSources`                                                                       | ✅     |
| `web/src/lib/connectors/credentials.ts`                                | `MondayCredentialJson` interface + template-default + display-name entries                                                          | ✅     |
| `web/src/lib/connectors/connectors.tsx`                                | `monday:` entry in `connectorConfigs`                                                                                               | ✅     |
| `web/src/lib/sources.ts`                                               | `SvgMonday` import + `monday` entry in `SOURCE_METADATA_MAP`                                                                        | ✅     |
| `web/lib/opal/src/logos/index.ts`                                      | `export { default as SvgMonday } ...` (one marked line)                                                                             | ✅     |
| `backend/tests/utils/secret_names.py`                                  | `MONDAY_API_TOKEN` in `TestSecret`                                                                                                  | ✅     |
| `backend/onyx/connectors/cross_connector_utils/miscellaneous_utils.py` | `DocumentSource.MONDAY` in `_SOURCE_METADATA_KEYS_TO_IGNORE` (`workspace_id`, `board_id`) `# FORK: monday`                          | ✅     |
| `backend/ee/onyx/external_permissions/monday/page_access.py`           | closed-workspace ACL merge for all board kinds + paginated subscriber fetch                                                         | ✅     |
| `backend/ee/onyx/search/process_search_query.py`                       | `_detect_monday_identifier_tag` + `_detect_exact_lookup_tag` for workspace/board id Tag fast path `# FORK: monday`                  | ✅     |

> **Why the registry is special:** instead of editing the `CONNECTOR_CLASS_MAP` dict body (which
> upstream edits on every connector PR → guaranteed conflicts), the fork adds a single merge-hook
> _after_ the dict and keeps the actual mapping in `fork_registry.py`. Future fork connectors only
> edit `fork_registry.py` — no further `registry.py` churn.

> **Why enums can't use the hook:** Python `DocumentSource` and TS `ValidSources` are enums and
> can't be extended at runtime, and DB columns / derived types key off them — so these edits are
> unavoidable. Keeping them at the end of the enum + marked makes any conflict a one-line re-add.

### Miro connector (visual asset boards, semantic + thumbnail search)

| File                                       | Edit                                                                                                                    | Status |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- | ------ |
| `backend/onyx/configs/constants.py`        | `MIRO = "miro"` at end of `DocumentSource` enum + entry in `DocumentSourceDescription`, both `# FORK: miro`             | ✅     |
| `backend/onyx/connectors/fork_registry.py` | `DocumentSource.MIRO` entry in `FORK_CONNECTOR_CLASS_MAP` (no `registry.py` edit needed — reuses the Monday merge-hook) | ✅     |
| `web/src/lib/types.ts`                     | `Miro = "miro"  // FORK: miro` at end of `ValidSources`                                                                 | ✅     |
| `web/src/lib/connectors/credentials.ts`    | `MiroCredentialJson` interface + template-default + display-name entries                                                | ✅     |
| `web/src/lib/connectors/connectors.tsx`    | `miro:` entry in `connectorConfigs`                                                                                     | ✅     |
| `web/src/lib/sources.ts`                   | `SvgMiro` import + `miro` entry in `SOURCE_METADATA_MAP`                                                                | ✅     |
| `web/lib/opal/src/logos/index.ts`          | `export { default as SvgMiro } ...` (one marked line)                                                                   | ✅     |

**Search-result thumbnails** (cross-cutting; carries `image_file_id` from the retrieved chunk
through to the frontend result model so matched image assets render as a thumbnail, not just a
text blurb):

| File                                                        | Edit                                                                                                                        | Status |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------ |
| `backend/onyx/context/search/models.py`                     | `image_file_id` field on `SearchDoc` + copy from `chunk.image_file_id` in `from_chunks_or_sections()`                       | ✅     |
| `backend/ee/onyx/server/query_and_chat/models.py`           | copy `chunk.image_file_id` in `SearchDocWithContent.from_inference_sections()` (field itself is inherited from `SearchDoc`) | ✅     |
| `web/src/lib/search/interfaces.ts`                          | `image_file_id` field on `OnyxDocument` + `SearchDocWithContent`                                                            | ✅     |
| `web/src/ee/sections/SearchCard.tsx`                        | render thumbnail via `buildImgUrl(image_file_id)`                                                                           | ✅     |
| `web/src/sections/document-sidebar/ChatDocumentDisplay.tsx` | render thumbnail via `buildImgUrl(image_file_id)`                                                                           | ✅     |
| `web/src/components/search/DocumentDisplay.tsx`             | render thumbnail via `buildImgUrl(image_file_id)` in `CompactDocumentCard`                                                  | ✅     |

> No `access.py` change was needed: the Miro connector sets `Document.file_id` equal to the
> stored image's file-store id (`image_file_id`), so the existing connector-ACL branch of
> `user_can_access_chat_file` already grants access to `GET /api/chat/file/{file_id}`.

**Search & asset fixes** (broken thumbnails, exact-match asset lookup, misleading dates,
indistinguishable results — see `plans/miro_search_fixes_a18f5ad6.plan.md`):

| File                                                        | Edit                                                                                                                                                                                                                                                                                                                                                                                                         | Status                         |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------ |
| `backend/onyx/db/user_file.py`                              | `get_file_id_by_user_file_id` returns `None` for non-UUID input instead of raising (fixes `GET /chat/file/{file_id}` 500 for connector file ids like `miro__<board>__<item>`)                                                                                                                                                                                                                                | ✅                             |
| `backend/onyx/connectors/miro/connector.py`                 | recover the real asset filename at download time (`Content-Disposition` header / redirected URL basename); index `asset_filename`/`miro_item_id`/`board_id` into `Document.metadata`; distinctive `semantic_identifier` fallback (`asset_title → asset_filename → "<frame/board> — <item ref>"`) and per-asset line in the context blurb, so results in the same frame no longer collapse to one title/blurb | net-new file, no marker needed |
| `backend/ee/onyx/search/process_search_query.py`            | `_detect_miro_identifier_tag` + `_maybe_exact_lookup` exact-match fast path (identifier-shaped queries run through a `Tag` filter via the normal `search_pipeline`, short-circuiting hybrid search); `populate_file_ids_on_sections` call after `merge_individual_chunks`                                                                                                                                    | ✅                             |
| `backend/ee/onyx/server/query_and_chat/models.py`           | populate the inherited `file_id` field (`file_id=chunk.file_id`) in `SearchDocWithContent.from_inference_sections()`                                                                                                                                                                                                                                                                                         | ✅                             |
| `web/src/lib/search/interfaces.ts`                          | new `file_id?` field on `OnyxDocument` + `SearchDocWithContent` (thumbnail fallback for when a text chunk is the top hit)                                                                                                                                                                                                                                                                                    | ✅                             |
| `web/src/ee/sections/SearchCard.tsx`                        | hide `updated_at` for Miro results (the API's `modifiedAt` is a bulk-import timestamp, not per-asset); thumbnail falls back to `image_file_id ?? file_id`                                                                                                                                                                                                                                                    | ✅                             |
| `web/src/sections/document-sidebar/ChatDocumentDisplay.tsx` | same date-hide + `image_file_id ?? file_id` fallback                                                                                                                                                                                                                                                                                                                                                         | ✅                             |
| `web/src/components/search/DocumentDisplay.tsx`             | same date-hide + `image_file_id ?? file_id` fallback in `CompactDocumentCard`                                                                                                                                                                                                                                                                                                                                | ✅                             |

> Why identifiers are exact-matched via a `Tag` filter and not `DocumentIndex.id_based_retrieval`:
> the tag route reuses `search_pipeline`'s ACL/tenant/censoring logic unchanged (`id_based_retrieval`
> takes a raw `IndexFilters` whose ACL handling is caller-owned and documented as temporary), and
> works uniformly for filename, item id, and full doc id since all three are indexed as
> `Document.metadata` on the connector side.

**Image indexing quality** (meaningful titles + rich, retrieval-optimized captions for visual
assets — see `plans/miro_image_indexing_fixes_fb9d8d96.plan.md`):

| File                                                  | Edit                                                                                                                                                                                                                                                                                                                                                                         | Status                         |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `backend/onyx/prompts/image_analysis.py`              | `DEFAULT_ASSET_CAPTION_SYSTEM_PROMPT` / `DEFAULT_ASSET_CAPTION_USER_PROMPT`: structured `TITLE:` / `DESCRIPTION:` captioning covering subject, on-image text, art style, colors, and layout                                                                                                                                                                                  | `# FORK: miro`                 |
| `backend/onyx/file_processing/image_summarization.py` | `ImageTitleAndSummary` model, `_parse_title_and_summary`, and `summarize_image_and_title_with_error_handling` (returns a short title + rich description; robust fallback when a small model doesn't follow the format)                                                                                                                                                       | `# FORK: miro`                 |
| `backend/onyx/connectors/models.py`                   | `DocumentBase.derive_title_from_image` opt-in flag                                                                                                                                                                                                                                                                                                                           | `# FORK: miro`                 |
| `backend/onyx/indexing/indexing_pipeline.py`          | `process_image_sections` uses the title-aware summarizer for docs that opt in; the LLM-derived short title replaces `title`/`semantic_identifier` (feeding `title_prefix`/`title_vector`), with the connector title kept as fallback                                                                                                                                         | `# FORK: miro`                 |
| `backend/onyx/connectors/miro/connector.py`           | `_is_meaningful_filename` / `_build_asset_title`: never title an asset with a placeholder filename (`image.png`, `download (1).jpg`, `screenshot.png`, …); build a deterministic board/frame/nearby-label title instead, and set `derive_title_from_image=True` for captionable assets. Placeholder filenames are also kept out of `Document.metadata` and the context blurb | net-new file, no marker needed |

> `doc_summary`/`chunk_context` intentionally stay empty for Miro image docs: they are
> Contextual-RAG fields, and these one-caption assets fit in a single chunk so that stage never
> runs. The searchable image signal lives in `content` (the caption) and the enriched
> `content_vector` (`title_prefix + content + metadata_suffix`).

**Image title/caption follow-ups** (persist the caption title to Postgres; harden fallback
title + vision timeout — see `plans/image_title_and_caption_a81aa9f9.plan.md`):

| File                                                                   | Edit                                                                                                                                                                                                                                                                                               | Status                             |
| ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `backend/onyx/db/document.py`                                          | `update_docs_semantic_id__no_commit`: write the image-derived title back to `Document.semantic_id`, since image summarization upgrades the title AFTER the initial upsert (otherwise the search index has the good title but the Postgres row stays on the pre-caption connector title)            | `# FORK: miro`                     |
| `backend/onyx/indexing/indexing_pipeline.py`                           | after the content-hash write-back, persist the final `semantic_identifier` to Postgres for docs with `derive_title_from_image=True` that were successfully indexed                                                                                                                                 | `# FORK: miro`                     |
| `backend/onyx/connectors/miro/connector.py`                            | `_is_meaningful_filename` also rejects UUID and long bare-hex stems (`7ff78985-…`, `95d9ddb8…`) so machine-generated ids never become the fallback title                                                                                                                                           | net-new file, no marker needed     |
| `backend/onyx/connectors/miro/connector.py`                            | `asset_filename` now always stored in `Document.metadata` (even for generic names like `image_720.png`) so every asset is findable by exact filename lookup; the filename is excluded from the embedded suffix via `_SOURCE_METADATA_KEYS_TO_IGNORE` — meaningful titles/embeddings are unaffected | net-new file, no marker needed     |
| `backend/onyx/connectors/cross_connector_utils/miscellaneous_utils.py` | Added `asset_filename` to `_SOURCE_METADATA_KEYS_TO_IGNORE[MIRO]` so it is a filterable exact-match tag but never embedded into the semantic/keyword suffix                                                                                                                                        | `# FORK: miro`                     |
| `backend/ee/onyx/search/process_search_query.py`                       | Broadened `_MIRO_ASSET_FILENAME_RE` from hex-only (16+ chars) to any single-token filename ending in an image extension (e.g. `image_720.png`, `Logo-Final.webp`) — routes to the exact `asset_filename` Tag lookup                                                                                | `# FORK: miro`                     |
| `backend/onyx/llm/factory.py`                                          | fix the `get_default_llm_with_vision` timeout guard (`if not None` was always true / would `TypeError` on `None`) to `timeout is not None`, keeping the 180s floor                                                                                                                                 | upstream file, no marker (bug fix) |
| `web/src/ee/sections/AssetImageLightbox.tsx`                           | new component — Radix Dialog lightbox for Miro search-result thumbnails; shows a larger image with zoom-in/out (buttons + scroll wheel), asset title as a clickable link, close on X or click-outside                                                                                              | net-new file, no marker needed     |
| `web/src/ee/sections/SearchCard.tsx`                                   | wire `AssetImageLightbox` to the thumbnail — clicking opens the lightbox (`e.stopPropagation` so the card's own click handler is unaffected); hide the `blurb`/description for Miro results (Gemini captions power search but should not be shown to users)                                        | `// FORK: miro`                    |
| `web/src/components/search/DocumentDisplay.tsx`                        | hide the `blurb` for Miro results (consistency with `SearchCard`)                                                                                                                                                                                                                                  | `// FORK: miro`                    |
| `web/src/sections/document-sidebar/ChatDocumentDisplay.tsx`            | hide the `blurb` for Miro results in the document preview sidebar                                                                                                                                                                                                                                  | `// FORK: miro`                    |

> Vision model is Gemini Flash (Vertex AI) via the admin default-vision-model setting; it follows
> the `TITLE:`/`DESCRIPTION:` prompt reliably. Re-captioning already-indexed assets requires
> clearing them first (delete + re-add the connector), because `get_docs_to_update`'s content-hash
> gate deliberately excludes LLM summaries and so skips otherwise-unchanged docs even on a full crawl.

**Auto Sync Permissions** (team-based external-group ACL — see
`plans/miro_auto_sync_permissions_9d9e7425.plan.md`):

| File                                                             | Edit                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Status                         |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `web/src/lib/types.ts`                                           | `ValidSources.Miro` added to `validAutoSyncSources`                                                                                                                                                                                                                                                                                                                                                                                                              | `// FORK: miro`                |
| `web/src/lib/connectors/AutoSyncOptionFields.tsx`                | `miro: {}` entry (required by `Record<ValidAutoSyncSource, ...>`)                                                                                                                                                                                                                                                                                                                                                                                                | `// FORK: miro`                |
| `backend/onyx/connectors/miro/connector.py`                      | `MiroConnector` now also implements `SlimConnectorWithPermSync`: `retrieve_all_slim_docs_perm_sync` tags each asset with its board's `ExternalAccess` (`_board_external_access`: org/public-link → public, team access → `miro_team_<team_id>` group, private-to-individuals → admin-only); org helpers `_get_org_id`, `_iter_paginated`, `get_team_member_email_map`, `probe_org_member_access`; optional `miro_org_id` (auto-discovered via `/v1/oauth-token`) | net-new file, no marker needed |
| `backend/ee/onyx/external_permissions/miro/doc_sync.py`          | `miro_doc_sync` (wraps `generic_doc_sync`, Monday pattern)                                                                                                                                                                                                                                                                                                                                                                                                       | net-new file                   |
| `backend/ee/onyx/external_permissions/miro/group_sync.py`        | `miro_group_sync` (teams → member emails → `ExternalUserGroup(miro_team_<id>)`)                                                                                                                                                                                                                                                                                                                                                                                  | net-new file                   |
| `backend/ee/onyx/external_permissions/sync_params.py`            | `DocumentSource.MIRO` entry in `_SOURCE_TO_SYNC_CONFIG` (doc sync + cc-pair-agnostic group sync) + imports                                                                                                                                                                                                                                                                                                                                                       | `# FORK: miro`                 |
| `backend/ee/onyx/configs/app_configs.py`                         | `MIRO_PERMISSION_DOC_SYNC_FREQUENCY` / `MIRO_PERMISSION_GROUP_SYNC_FREQUENCY` (default 30 min)                                                                                                                                                                                                                                                                                                                                                                   | `# FORK: miro`                 |
| `backend/ee/onyx/connectors/perm_sync_valid.py`                  | `validate_miro_perm_sync` branch — probes the org-members API so a non-org token fails fast                                                                                                                                                                                                                                                                                                                                                                      | `# FORK: miro`                 |
| `backend/tests/unit/onyx/connectors/miro/test_miro_perm_sync.py` | board sharing-policy -> ExternalAccess, team-member email join, slim-doc perm sync (all Miro API mocked)                                                                                                                                                                                                                                                                                                                                                         | net-new file                   |

> Perm sync requires an **organization-scoped** Miro token (indexing works with a board-only
> token). Access is team-based: boards grant access via `sharingPolicy.teamAccess` on the board's
> team, and member emails come from `GET /v2/orgs/{org_id}/members` (board-member IDs are in a
> separate user-id space with no email endpoint, so per-individual board shares fall back to their
> team group or admin-only — fails closed, never over-shares). `initial_index_should_sync=False`:
> assets index first, then the beat doc-permission + external-group sync jobs apply access.
