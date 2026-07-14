from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import cast

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from onyx.auth.permissions import require_permission
from onyx.auth.users import current_curator_or_admin_user
from onyx.background.celery.versioned_apps.client import app as client_app
from onyx.background.indexing.models import IndexAttemptErrorPydantic
from onyx.configs.app_configs import GENERATIVE_MODEL_ACCESS_CHECK_FREQ
from onyx.configs.constants import DocumentSource
from onyx.configs.constants import KV_GEN_AI_KEY_CHECK_TIME
from onyx.configs.constants import OnyxCeleryPriority
from onyx.configs.constants import OnyxCeleryTask
from onyx.configs.constants import PUBLIC_API_TAGS
from onyx.db.connector_credential_pair import bulk_set_cc_pair_status
from onyx.db.connector_credential_pair import get_connector_credential_pair_for_user
from onyx.db.connector_credential_pair import (
    get_connector_credential_pairs_for_user,
)
from onyx.db.connector_credential_pair import update_connector_credential_pair_from_id
from onyx.db.engine.sql_engine import get_session
from onyx.db.enums import ConnectorCredentialPairStatus
from onyx.db.enums import Permission
from onyx.db.feedback import fetch_docs_ranked_by_boost_for_user
from onyx.db.feedback import update_document_boost_for_user
from onyx.db.feedback import update_document_hidden_for_user
from onyx.db.index_attempt import bulk_cancel_indexing_attempts_for_ccpairs
from onyx.db.index_attempt import cancel_indexing_attempts_for_ccpair
from onyx.db.index_attempt import get_index_attempt_errors_across_connectors
from onyx.db.models import ConnectorCredentialPair
from onyx.db.models import User
from onyx.file_store.file_store import get_default_file_store
from onyx.key_value_store.factory import get_kv_store
from onyx.key_value_store.interface import KvKeyNotFoundError
from onyx.llm.factory import get_default_llm
from onyx.llm.utils import test_llm
from onyx.server.documents.models import BulkActionItemResult
from onyx.server.documents.models import BulkActionOutcome
from onyx.server.documents.models import BulkActionResponse
from onyx.server.documents.models import BulkDeletionRequest
from onyx.server.documents.models import ConnectorCredentialPairIdentifier
from onyx.server.documents.models import PaginatedReturn
from onyx.server.manage.models import BoostDoc
from onyx.server.manage.models import BoostUpdateRequest
from onyx.server.manage.models import HiddenUpdateRequest
from onyx.server.models import StatusResponse
from onyx.utils.logger import setup_logger
from shared_configs.contextvars import get_current_tenant_id

router = APIRouter(prefix="/manage")
logger = setup_logger()

"""Admin only API endpoints"""


@router.get("/admin/doc-boosts")
def get_most_boosted_docs(
    ascending: bool,
    limit: int,
    user: User = Depends(current_curator_or_admin_user),
    db_session: Session = Depends(get_session),
) -> list[BoostDoc]:
    boost_docs = fetch_docs_ranked_by_boost_for_user(
        ascending=ascending,
        limit=limit,
        db_session=db_session,
        user=user,
    )
    return [
        BoostDoc(
            document_id=doc.id,
            semantic_id=doc.semantic_id,
            # source=doc.source,
            link=doc.link or "",
            boost=doc.boost,
            hidden=doc.hidden,
        )
        for doc in boost_docs
    ]


@router.post("/admin/doc-boosts")
def document_boost_update(
    boost_update: BoostUpdateRequest,
    user: User = Depends(current_curator_or_admin_user),
    db_session: Session = Depends(get_session),
) -> StatusResponse:
    update_document_boost_for_user(
        db_session=db_session,
        document_id=boost_update.document_id,
        boost=boost_update.boost,
        user=user,
    )
    return StatusResponse(success=True, message="Updated document boost")


@router.post("/admin/doc-hidden")
def document_hidden_update(
    hidden_update: HiddenUpdateRequest,
    user: User = Depends(current_curator_or_admin_user),
    db_session: Session = Depends(get_session),
) -> StatusResponse:
    update_document_hidden_for_user(
        db_session=db_session,
        document_id=hidden_update.document_id,
        hidden=hidden_update.hidden,
        user=user,
    )
    return StatusResponse(success=True, message="Updated document boost")


@router.get("/admin/genai-api-key/validate")
def validate_existing_genai_api_key(
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
) -> None:
    # Only validate every so often
    kv_store = get_kv_store()
    curr_time = datetime.now(tz=timezone.utc)
    try:
        last_check = datetime.fromtimestamp(
            cast(float, kv_store.load(KV_GEN_AI_KEY_CHECK_TIME)), tz=timezone.utc
        )
        check_freq_sec = timedelta(seconds=GENERATIVE_MODEL_ACCESS_CHECK_FREQ)
        if curr_time - last_check < check_freq_sec:
            return
    except KvKeyNotFoundError:
        # First time checking the key, nothing unusual
        pass

    try:
        llm = get_default_llm(timeout=10)
    except ValueError:
        raise HTTPException(status_code=404, detail="LLM not setup")

    error = test_llm(llm)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Mark check as successful
    curr_time = datetime.now(tz=timezone.utc)
    kv_store.store(KV_GEN_AI_KEY_CHECK_TIME, curr_time.timestamp())


@router.post("/admin/deletion-attempt", tags=PUBLIC_API_TAGS)
def create_deletion_attempt_for_connector_id(
    connector_credential_pair_identifier: ConnectorCredentialPairIdentifier,
    user: User = Depends(current_curator_or_admin_user),
    db_session: Session = Depends(get_session),
) -> None:
    tenant_id = get_current_tenant_id()

    connector_id = connector_credential_pair_identifier.connector_id
    credential_id = connector_credential_pair_identifier.credential_id

    cc_pair = get_connector_credential_pair_for_user(
        db_session=db_session,
        connector_id=connector_id,
        credential_id=credential_id,
        user=user,
        get_editable=True,
    )
    if cc_pair is None:
        error = f"Connector with ID '{connector_id}' and credential ID '{credential_id}' does not exist. Has it already been deleted?"
        logger.error(error)
        raise HTTPException(
            status_code=404,
            detail=error,
        )

    # Cancel any scheduled indexing attempts
    cancel_indexing_attempts_for_ccpair(
        cc_pair_id=cc_pair.id, db_session=db_session, include_secondary_index=True
    )

    # TODO(rkuo): 2024-10-24 - check_deletion_attempt_is_allowed shouldn't be necessary
    # any more due to background locking improvements.
    # Remove the below permanently if everything is behaving for 30 days.

    # Check if the deletion attempt should be allowed
    # deletion_attempt_disallowed_reason = check_deletion_attempt_is_allowed(
    #     connector_credential_pair=cc_pair, db_session=db_session
    # )
    # if deletion_attempt_disallowed_reason:
    #     raise HTTPException(
    #         status_code=400,
    #         detail=deletion_attempt_disallowed_reason,
    #     )

    # mark as deleting
    update_connector_credential_pair_from_id(
        db_session=db_session,
        cc_pair_id=cc_pair.id,
        status=ConnectorCredentialPairStatus.DELETING,
    )

    db_session.commit()

    # run the beat task to pick up this deletion from the db immediately
    client_app.send_task(
        OnyxCeleryTask.CHECK_FOR_CONNECTOR_DELETION,
        priority=OnyxCeleryPriority.HIGH,
        kwargs={"tenant_id": tenant_id},
    )

    logger.info(
        "create_deletion_attempt_for_connector_id - running check_for_connector_deletion: cc_pair=%s",
        cc_pair.id,
    )

    if cc_pair.connector.source == DocumentSource.FILE:
        connector = cc_pair.connector
        file_store = get_default_file_store()
        for file_id in connector.connector_specific_config.get("file_locations", []):
            file_store.delete_file(file_id)


# Bounded concurrency for the only slow per-connector work in bulk delete:
# File-source file-store (S3/MinIO) deletes. Everything else is set-based SQL.
_BULK_FILE_DELETE_MAX_WORKERS = 8

# A connector may be deleted only from these statuses (mirrors the
# single-connector `statusIsNotCurrentlyActive` rule).
_DELETABLE_STATUSES = {
    ConnectorCredentialPairStatus.PAUSED,
    ConnectorCredentialPairStatus.INVALID,
}


@router.post("/admin/bulk-deletion-attempt", tags=PUBLIC_API_TAGS)
def bulk_create_deletion_attempt(
    bulk_request: BulkDeletionRequest,
    user: User = Depends(current_curator_or_admin_user),
    db_session: Session = Depends(get_session),
) -> BulkActionResponse:
    """Schedule deletion for every editable connector of a single vendor
    (source) that is eligible, returning a per-connector outcome.

    Requires connectors to be paused first: a still-active connector is
    REJECTED (untouched), not deleted. A connector already DELETING is SKIPPED.
    File-source backing files are removed post-commit via a bounded pool; a
    file-cleanup failure downgrades that connector to WARNING (its deletion is
    still in motion) rather than failing it.
    """
    tenant_id = get_current_tenant_id()

    cc_pairs = get_connector_credential_pairs_for_user(
        db_session=db_session,
        user=user,
        get_editable=True,
        source=bulk_request.source,
        eager_load_connector=True,
    )

    results: list[BulkActionItemResult] = []
    eligible: list[ConnectorCredentialPair] = []
    for cc_pair in cc_pairs:
        if cc_pair.status == ConnectorCredentialPairStatus.DELETING:
            results.append(
                BulkActionItemResult(
                    cc_pair_id=cc_pair.id,
                    name=cc_pair.name,
                    outcome=BulkActionOutcome.SKIPPED,
                    message="Connector is already being deleted",
                )
            )
        elif cc_pair.status in _DELETABLE_STATUSES:
            eligible.append(cc_pair)
        else:
            results.append(
                BulkActionItemResult(
                    cc_pair_id=cc_pair.id,
                    name=cc_pair.name,
                    outcome=BulkActionOutcome.REJECTED,
                    message="Connector must be paused before deletion",
                )
            )

    if eligible:
        eligible_ids = [cc_pair.id for cc_pair in eligible]

        # Capture plain data BEFORE commit so the post-commit file-delete
        # threads never touch the (non-thread-safe) ORM session.
        result_by_id: dict[int, BulkActionItemResult] = {}
        file_delete_plan: list[tuple[int, list[str]]] = []
        for cc_pair in eligible:
            item = BulkActionItemResult(
                cc_pair_id=cc_pair.id,
                name=cc_pair.name,
                outcome=BulkActionOutcome.SUCCEEDED,
            )
            result_by_id[cc_pair.id] = item
            results.append(item)

            if cc_pair.connector.source == DocumentSource.FILE:
                file_ids = (
                    cc_pair.connector.connector_specific_config.get(
                        "file_locations", []
                    )
                    or []
                )
                if file_ids:
                    file_delete_plan.append((cc_pair.id, file_ids))

        # Set-based: cancel in-flight attempts + mark DELETING, then a single
        # commit and a single deletion-check kick.
        bulk_cancel_indexing_attempts_for_ccpairs(
            cc_pair_ids=eligible_ids,
            db_session=db_session,
            include_secondary_index=True,
        )
        bulk_set_cc_pair_status(
            db_session=db_session,
            cc_pair_ids=eligible_ids,
            status=ConnectorCredentialPairStatus.DELETING,
        )
        db_session.commit()

        client_app.send_task(
            OnyxCeleryTask.CHECK_FOR_CONNECTOR_DELETION,
            priority=OnyxCeleryPriority.HIGH,
            kwargs={"tenant_id": tenant_id},
        )

        # Post-commit best-effort file cleanup (File source only). Errors are
        # per-connector and downgrade to WARNING; they never roll back the
        # already-committed DELETING status nor abort sibling connectors.
        if file_delete_plan:

            def _delete_backing_files(file_ids: list[str]) -> None:
                file_store = get_default_file_store()
                for file_id in file_ids:
                    file_store.delete_file(file_id)

            with ThreadPoolExecutor(
                max_workers=_BULK_FILE_DELETE_MAX_WORKERS
            ) as executor:
                future_to_id = {
                    executor.submit(_delete_backing_files, file_ids): cc_pair_id
                    for cc_pair_id, file_ids in file_delete_plan
                }
                for future in as_completed(future_to_id):
                    cc_pair_id = future_to_id[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.exception(
                            "Bulk delete: file cleanup failed for cc_pair=%s",
                            cc_pair_id,
                        )
                        item = result_by_id[cc_pair_id]
                        item.outcome = BulkActionOutcome.WARNING
                        item.message = (
                            "Connector scheduled for deletion, but file cleanup "
                            f"failed: {e}"
                        )

    return BulkActionResponse.from_results(results)


@router.get("/admin/indexing/failed-documents")
def get_failed_documents(
    cc_pair_id: int | None = None,
    error_type: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    include_resolved: bool = False,
    page_num: int = 0,
    page_size: int = 25,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> PaginatedReturn[IndexAttemptErrorPydantic]:
    """Get indexing errors across all connectors with optional filters.

    Provides a cross-connector view of document indexing failures.
    Defaults to last 30 days if no start_time is provided to avoid
    unbounded count queries.
    """
    if start_time is None:
        start_time = datetime.now(tz=timezone.utc) - timedelta(days=30)

    errors, total = get_index_attempt_errors_across_connectors(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        error_type=error_type,
        start_time=start_time,
        end_time=end_time,
        unresolved_only=not include_resolved,
        page=page_num,
        page_size=page_size,
    )
    return PaginatedReturn(
        items=[IndexAttemptErrorPydantic.from_model(e) for e in errors],
        total_items=total,
    )
