import re
from collections.abc import Generator

from sqlalchemy.orm import Session

from ee.onyx.db.search import create_search_query
from ee.onyx.secondary_llm_flows.query_expansion import expand_keywords
from ee.onyx.server.query_and_chat.models import SearchDocWithContent
from ee.onyx.server.query_and_chat.models import SearchFullResponse
from ee.onyx.server.query_and_chat.models import SendSearchQueryRequest
from ee.onyx.server.query_and_chat.streaming_models import LLMSelectedDocsPacket
from ee.onyx.server.query_and_chat.streaming_models import SearchDocsPacket
from ee.onyx.server.query_and_chat.streaming_models import SearchErrorPacket
from ee.onyx.server.query_and_chat.streaming_models import SearchQueriesPacket
from onyx.context.search.models import BaseFilters
from onyx.context.search.models import ChunkSearchRequest
from onyx.context.search.models import InferenceChunk
from onyx.context.search.models import Tag
from onyx.context.search.pipeline import merge_individual_chunks
from onyx.context.search.pipeline import search_pipeline
from onyx.context.search.utils import populate_file_ids_on_sections
from onyx.db.models import User
from onyx.db.search_settings import get_current_search_settings
from onyx.document_index.factory import get_default_document_index
from onyx.document_index.interfaces_new import DocumentIndex
from onyx.llm.factory import get_default_llm
from onyx.secondary_llm_flows.document_filter import select_sections_for_expansion
from onyx.tools.tool_implementations.search.search_utils import (
    weighted_reciprocal_rank_fusion,
)
from onyx.utils.logger import setup_logger
from onyx.utils.threadpool_concurrency import run_functions_tuples_in_parallel

logger = setup_logger()


# This is just a heuristic that also happens to work well for the UI/UX
# Users would not find it useful to see a huge list of suggested docs
# but more than 1 is also likely good as many questions may target more than 1 doc.
TARGET_NUM_SECTIONS_FOR_LLM_SELECTION = 3


# FORK: miro - exact-match fast path for identifier-shaped queries. See
# _maybe_exact_lookup below. Matches any single-token filename ending in a
# recognised image extension (no whitespace), covering both hex-hash names
# (e.g. "1ab6ca7c7db07f88.png") and human-named files (e.g. "image_720.png",
# "Logo-Final.webp"). Multi-word queries are left to normal hybrid search.
_MIRO_ASSET_FILENAME_RE = re.compile(
    r"^[\w.\-]+\.(jpg|jpeg|png|webp|svg|gif)$", re.IGNORECASE
)
_MIRO_DOC_ID_PREFIX = "miro__"
# Bare Miro item ids are opaque alphanumeric/base64-like tokens (e.g.
# "uXjVH_7LB9o="); requiring a digit is a cheap way to avoid misclassifying
# plain english search terms (which rarely contain digits) as an identifier.
_BARE_MIRO_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_=-]{8,}$")


def _detect_miro_identifier_tag(query: str) -> Tag | None:
    """Classifies a search query as a Miro asset filename, full document id,
    or bare item id, returning the exact-match `Tag` to filter on. Returns
    `None` when the query looks like free text (e.g. contains whitespace),
    so the caller runs normal hybrid search instead.
    """
    stripped = query.strip()
    if not stripped or " " in stripped:
        return None

    if _MIRO_ASSET_FILENAME_RE.match(stripped):
        return Tag(tag_key="asset_filename", tag_value=stripped)

    if stripped.startswith(_MIRO_DOC_ID_PREFIX):
        # doc id shape is "miro__<board_id>__<item_id>"; the item id is
        # reliably the segment after the last "__".
        remainder = stripped[len(_MIRO_DOC_ID_PREFIX) :]
        if "__" in remainder:
            item_id = remainder.rsplit("__", 1)[-1]
            if item_id:
                return Tag(tag_key="miro_item_id", tag_value=item_id)
        return None

    if _BARE_MIRO_ITEM_ID_RE.match(stripped) and any(c.isdigit() for c in stripped):
        return Tag(tag_key="miro_item_id", tag_value=stripped)

    return None


def _run_single_search(
    query: str,
    filters: BaseFilters | None,
    document_index: DocumentIndex,
    user: User,
    db_session: Session,
    num_hits: int | None = None,
    hybrid_alpha: float | None = None,
) -> list[InferenceChunk]:
    """Execute a single search query and return chunks."""
    chunk_search_request = ChunkSearchRequest(
        query=query,
        user_selected_filters=filters,
        limit=num_hits,
        hybrid_alpha=hybrid_alpha,
    )

    return search_pipeline(
        chunk_search_request=chunk_search_request,
        document_index=document_index,
        user=user,
        persona_search_info=None,
        db_session=db_session,
    )


# FORK: miro
def _maybe_exact_lookup(
    request: SendSearchQueryRequest,
    document_index: DocumentIndex,
    user: User,
    db_session: Session,
) -> list[InferenceChunk] | None:
    """Exact-match fast path for identifier-shaped queries (Miro asset
    filename / item id / full doc id). When the query is detected as an
    identifier, runs a search scoped to an exact `Tag` filter on the
    connector-indexed identifier metadata (see
    onyx/connectors/miro/connector.py::_item_to_document), reusing the normal
    `search_pipeline` so ACL, tenant scoping, and post-query censoring are
    unchanged.

    Returns `None` (never an empty list) both when the query doesn't look
    like an identifier and when the identifier has no match, so the caller
    always falls through to normal hybrid search rather than showing no
    results for a mistyped identifier.
    """
    tag = _detect_miro_identifier_tag(request.search_query)
    if tag is None:
        return None

    base_filters = request.filters or BaseFilters()
    exact_filters = base_filters.model_copy(
        update={"tags": [*(base_filters.tags or []), tag]}
    )

    chunks = _run_single_search(
        query=request.search_query,
        filters=exact_filters,
        document_index=document_index,
        user=user,
        db_session=db_session,
        num_hits=request.num_hits,
        hybrid_alpha=request.hybrid_alpha,
    )
    return chunks or None


def stream_search_query(
    request: SendSearchQueryRequest,
    user: User,
    db_session: Session,
) -> Generator[
    SearchQueriesPacket | SearchDocsPacket | LLMSelectedDocsPacket | SearchErrorPacket,
    None,
    None,
]:
    """
    Core search function that yields streaming packets.
    Used by both streaming and non-streaming endpoints.
    """
    # Get document index.
    search_settings = get_current_search_settings(db_session)
    # This flow is for search so we do not get all indices.
    document_index = get_default_document_index(search_settings, None, db_session)

    # Determine queries to execute
    original_query = request.search_query
    keyword_expansions: list[str] = []

    # FORK: miro - exact-match fast path for identifier-shaped queries.
    # Skips query expansion entirely when matched, since expanding a pasted
    # filename/id into keyword queries would only reintroduce fuzzy matches.
    exact_chunks = _maybe_exact_lookup(request, document_index, user, db_session)

    if exact_chunks is None and request.run_query_expansion:
        try:
            llm = get_default_llm()
            keyword_expansions = expand_keywords(
                user_query=original_query,
                llm=llm,
            )
            if keyword_expansions:
                logger.debug(
                    "Query expansion generated %s keyword queries",
                    len(keyword_expansions),
                )
        except Exception as e:
            logger.warning("Query expansion failed: %s; using original query only.", e)
            keyword_expansions = []

    # Build list of all executed queries for tracking
    all_executed_queries = [original_query] + keyword_expansions

    if not user.is_anonymous:
        create_search_query(
            db_session=db_session,
            user_id=user.id,
            query=request.search_query,
            query_expansions=keyword_expansions if keyword_expansions else None,
        )

    # Execute search(es)
    if exact_chunks is not None:
        chunks = exact_chunks
    elif not keyword_expansions:
        # Single query (original only) - no threading needed
        chunks = _run_single_search(
            query=original_query,
            filters=request.filters,
            document_index=document_index,
            user=user,
            db_session=db_session,
            num_hits=request.num_hits,
            hybrid_alpha=request.hybrid_alpha,
        )
    else:
        # Multiple queries - run in parallel and merge with RRF
        # First query is the original (semantic), rest are keyword expansions
        search_functions = [
            (
                _run_single_search,
                (
                    query,
                    request.filters,
                    document_index,
                    user,
                    db_session,
                    request.num_hits,
                    request.hybrid_alpha,
                ),
            )
            for query in all_executed_queries
        ]

        # Run all searches in parallel
        all_search_results: list[list[InferenceChunk]] = (
            run_functions_tuples_in_parallel(
                search_functions,
                allow_failures=True,
            )
        )

        # Separate original query results from keyword expansion results
        # Note that in rare cases, the original query may have failed and so we may be
        # just overweighting one set of keyword results, should be not a big deal though.
        original_result = all_search_results[0] if all_search_results else []
        keyword_results = all_search_results[1:] if len(all_search_results) > 1 else []

        # Build valid results and weights
        # Original query (semantic): weight 2.0
        # Keyword expansions: weight 1.0 each
        valid_results: list[list[InferenceChunk]] = []
        weights: list[float] = []

        if original_result:
            valid_results.append(original_result)
            weights.append(2.0)

        for keyword_result in keyword_results:
            if keyword_result:
                valid_results.append(keyword_result)
                weights.append(1.0)

        if not valid_results:
            logger.warning("All parallel searches returned empty results")
            chunks = []
        else:
            chunks = weighted_reciprocal_rank_fusion(
                ranked_results=valid_results,
                weights=weights,
                id_extractor=lambda chunk: f"{chunk.document_id}_{chunk.chunk_id}",
            )

    # Merge chunks into sections
    sections = merge_individual_chunks(chunks)

    # Truncate to the requested number of hits
    sections = sections[: request.num_hits]

    # FORK: miro - stamp Document.file_id (Postgres-only, not in the vector
    # index) onto each chunk. Used as a thumbnail fallback for the rare case
    # where a text chunk (no image_file_id) is the top hit for an image doc.
    populate_file_ids_on_sections(sections, db_session)

    # Apply LLM document selection if requested
    # num_docs_fed_to_llm_selection specifies how many sections to feed to the LLM for selection
    # The LLM will always try to select TARGET_NUM_SECTIONS_FOR_LLM_SELECTION sections from those fed to it
    # llm_selected_doc_ids will be:
    #   - None if LLM selection was not requested or failed
    #   - Empty list if LLM selection ran but selected nothing
    #   - List of doc IDs if LLM selection succeeded
    run_llm_selection = (
        request.num_docs_fed_to_llm_selection is not None
        and request.num_docs_fed_to_llm_selection >= 1
    )
    llm_selected_doc_ids: list[str] | None = None
    llm_selection_failed = False
    if run_llm_selection and sections:
        try:
            llm = get_default_llm()
            sections_to_evaluate = sections[: request.num_docs_fed_to_llm_selection]
            selected_sections, _ = select_sections_for_expansion(
                sections=sections_to_evaluate,
                user_query=original_query,
                llm=llm,
                max_sections=TARGET_NUM_SECTIONS_FOR_LLM_SELECTION,
                try_to_fill_to_max=True,
            )
            # Extract unique document IDs from selected sections (may be empty)
            llm_selected_doc_ids = list(
                dict.fromkeys(
                    section.center_chunk.document_id for section in selected_sections
                )
            )
            logger.debug(
                "LLM document selection evaluated %s sections, selected %s sections with doc IDs: %s",
                len(sections_to_evaluate),
                len(selected_sections),
                llm_selected_doc_ids,
            )
        except Exception as e:
            # Allowing a blanket exception here as this step is not critical and the rest of the results are still valid
            logger.warning("LLM document selection failed: %s", e)
            llm_selection_failed = True
    elif run_llm_selection and not sections:
        # LLM selection requested but no sections to evaluate
        llm_selected_doc_ids = []

    # Convert to SearchDocWithContent list, optionally including content
    search_docs = SearchDocWithContent.from_inference_sections(
        sections,
        include_content=request.include_content,
        is_internet=False,
    )

    # Yield queries packet
    yield SearchQueriesPacket(all_executed_queries=all_executed_queries)

    # Yield docs packet
    yield SearchDocsPacket(search_docs=search_docs)

    # Yield LLM selected docs packet if LLM selection was requested
    # - llm_selected_doc_ids is None if selection failed
    # - llm_selected_doc_ids is empty list if no docs were selected
    # - llm_selected_doc_ids is list of IDs if docs were selected
    if run_llm_selection:
        yield LLMSelectedDocsPacket(
            llm_selected_doc_ids=None if llm_selection_failed else llm_selected_doc_ids
        )


def gather_search_stream(
    packets: Generator[
        SearchQueriesPacket
        | SearchDocsPacket
        | LLMSelectedDocsPacket
        | SearchErrorPacket,
        None,
        None,
    ],
) -> SearchFullResponse:
    """
    Aggregate all streaming packets into SearchFullResponse.
    """
    all_executed_queries: list[str] = []
    search_docs: list[SearchDocWithContent] = []
    llm_selected_doc_ids: list[str] | None = None
    error: str | None = None

    for packet in packets:
        if isinstance(packet, SearchQueriesPacket):
            all_executed_queries = packet.all_executed_queries
        elif isinstance(packet, SearchDocsPacket):
            search_docs = packet.search_docs
        elif isinstance(packet, LLMSelectedDocsPacket):
            llm_selected_doc_ids = packet.llm_selected_doc_ids
        elif isinstance(packet, SearchErrorPacket):
            error = packet.error

    return SearchFullResponse(
        all_executed_queries=all_executed_queries,
        search_docs=search_docs,
        doc_selection_reasoning=None,
        llm_selected_doc_ids=llm_selected_doc_ids,
        error=error,
    )
