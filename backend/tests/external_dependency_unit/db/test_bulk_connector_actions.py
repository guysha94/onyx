"""External dependency unit tests for the vendor-row bulk connector actions.

Covers the two outcomes that must NOT collapse into a single "failed" bucket
for bulk delete:

* REJECTED — a still-active connector is left completely untouched (the
  precondition "must be paused first" wasn't met).
* WARNING — the connector's deletion genuinely went through (DELETING is
  committed) but a post-commit File-source file-store cleanup call failed.
  This is the one place mocking `file_store.delete_file` is justified: we
  can't make MinIO/S3 fail on demand, and it's also the only path that
  exercises the bounded `ThreadPoolExecutor` used for bulk-delete file
  cleanup.

We invoke the FastAPI route functions directly with a constructed admin
`User` and the test `db_session`, matching the pattern used elsewhere in
`external_dependency_unit` (see `test_cc_pair_sync_attempts_routes.py`).
Assertions are scoped to the cc_pair IDs each test creates (rather than
aggregate response counts) so leftover rows from other tests sharing the
same Postgres instance can't make this test flaky.
"""

from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from onyx.configs.constants import DocumentSource
from onyx.connectors.models import InputType
from onyx.db.enums import AccessType
from onyx.db.enums import ConnectorCredentialPairStatus
from onyx.db.models import Connector
from onyx.db.models import ConnectorCredentialPair
from onyx.db.models import Credential
from onyx.db.models import User
from onyx.db.models import UserRole
from onyx.server.documents.cc_pair import bulk_update_cc_pair_status
from onyx.server.documents.models import BulkActionItemResult
from onyx.server.documents.models import BulkActionOutcome
from onyx.server.documents.models import BulkActionResponse
from onyx.server.documents.models import BulkCCStatusUpdateRequest
from onyx.server.documents.models import BulkDeletionRequest
from onyx.server.manage.administrative import bulk_create_deletion_attempt
from tests.external_dependency_unit.conftest import create_test_user
from tests.external_dependency_unit.indexing_helpers import cleanup_cc_pair

# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _create_cc_pair(
    db_session: Session,
    source: DocumentSource,
    status: ConnectorCredentialPairStatus,
    connector_specific_config: dict[str, Any] | None = None,
) -> ConnectorCredentialPair:
    suffix = uuid4().hex[:8]
    connector = Connector(
        name=f"test-connector-{suffix}",
        source=source,
        input_type=InputType.LOAD_STATE,
        connector_specific_config=connector_specific_config or {},
        refresh_freq=None,
        prune_freq=None,
        indexing_start=None,
    )
    db_session.add(connector)
    db_session.flush()

    credential = Credential(source=source, credential_json={})
    db_session.add(credential)
    db_session.flush()

    pair = ConnectorCredentialPair(
        connector_id=connector.id,
        credential_id=credential.id,
        name=f"test-cc-pair-{suffix}",
        status=status,
        access_type=AccessType.PUBLIC,
    )
    db_session.add(pair)
    db_session.commit()
    db_session.refresh(pair)
    return pair


def _admin_user(db_session: Session) -> User:
    return create_test_user(db_session, "admin", role=UserRole.ADMIN)


def _results_by_id(
    response: BulkActionResponse, ids: set[int]
) -> dict[int, BulkActionItemResult]:
    """Scope assertions to the cc_pairs this test created, ignoring any
    leftover rows of the same source from other tests on a shared DB."""
    return {r.cc_pair_id: r for r in response.results if r.cc_pair_id in ids}


def _refresh_status(
    db_session: Session, cc_pair: ConnectorCredentialPair
) -> ConnectorCredentialPairStatus:
    db_session.expire_all()
    refreshed = db_session.get(ConnectorCredentialPair, cc_pair.id)
    assert refreshed is not None
    return refreshed.status


@pytest.fixture(autouse=True)
def _redis_and_tenant(tenant_context: None) -> None:  # noqa: ARG001
    """Both routes call get_current_tenant_id(); bulk-status additionally
    touches Redis. tenant_context alone is sufficient for the delete-only
    tests in this file."""


# ---------------------------------------------------------------------------
# Delete: REJECTED (not paused) vs SUCCEEDED (paused) partition
# ---------------------------------------------------------------------------


class TestBulkDeleteRejectedVsSucceededPartition:
    def test_active_connector_is_rejected_and_left_intact(
        self,
        db_session: Session,
    ) -> None:
        admin = _admin_user(db_session)
        source = DocumentSource.MOCK_CONNECTOR

        active_pair = _create_cc_pair(
            db_session, source, ConnectorCredentialPairStatus.ACTIVE
        )
        paused_pair = _create_cc_pair(
            db_session, source, ConnectorCredentialPairStatus.PAUSED
        )
        try:
            with patch(
                "onyx.server.manage.administrative.client_app.send_task"
            ):
                response = bulk_create_deletion_attempt(
                    bulk_request=BulkDeletionRequest(source=source),
                    user=admin,
                    db_session=db_session,
                )

            results = _results_by_id(
                response, {active_pair.id, paused_pair.id}
            )

            active_result = results[active_pair.id]
            assert active_result.outcome == BulkActionOutcome.REJECTED
            assert active_result.message is not None
            assert "paused" in active_result.message.lower()

            paused_result = results[paused_pair.id]
            assert paused_result.outcome == BulkActionOutcome.SUCCEEDED

            # The active connector must be completely untouched.
            assert (
                _refresh_status(db_session, active_pair)
                == ConnectorCredentialPairStatus.ACTIVE
            )
            # The paused connector's deletion must have actually gone through.
            assert (
                _refresh_status(db_session, paused_pair)
                == ConnectorCredentialPairStatus.DELETING
            )
        finally:
            db_session.rollback()
            cleanup_cc_pair(db_session, active_pair)
            cleanup_cc_pair(db_session, paused_pair)

    def test_already_deleting_connector_is_skipped_not_failed(
        self,
        db_session: Session,
    ) -> None:
        admin = _admin_user(db_session)
        source = DocumentSource.MOCK_CONNECTOR

        deleting_pair = _create_cc_pair(
            db_session, source, ConnectorCredentialPairStatus.DELETING
        )
        try:
            with patch(
                "onyx.server.manage.administrative.client_app.send_task"
            ):
                response = bulk_create_deletion_attempt(
                    bulk_request=BulkDeletionRequest(source=source),
                    user=admin,
                    db_session=db_session,
                )

            results = _results_by_id(response, {deleting_pair.id})
            assert results[deleting_pair.id].outcome == BulkActionOutcome.SKIPPED
        finally:
            db_session.rollback()
            cleanup_cc_pair(db_session, deleting_pair)


# ---------------------------------------------------------------------------
# Delete: File-source cleanup failure -> WARNING (not FAILED), bounded pool
# ---------------------------------------------------------------------------


class TestBulkDeleteFileSourceCleanupWarning:
    def test_file_delete_failure_downgrades_to_warning_without_aborting_batch(
        self,
        db_session: Session,
    ) -> None:
        """One connector's file cleanup raises; it must still reach DELETING
        (status already committed pre-cleanup) and be reported as WARNING,
        not FAILED. Sibling connectors must be unaffected and also reach
        DELETING. Exercises the bounded ThreadPoolExecutor used for the
        post-commit file-store deletes."""
        admin = _admin_user(db_session)
        source = DocumentSource.FILE

        failing_file_id = f"file-{uuid4().hex[:8]}"
        failing_pair = _create_cc_pair(
            db_session,
            source,
            ConnectorCredentialPairStatus.PAUSED,
            connector_specific_config={"file_locations": [failing_file_id]},
        )
        sibling_pairs = [
            _create_cc_pair(
                db_session,
                source,
                ConnectorCredentialPairStatus.PAUSED,
                connector_specific_config={
                    "file_locations": [f"file-{uuid4().hex[:8]}"]
                },
            )
            for _ in range(3)
        ]
        all_pairs = [failing_pair, *sibling_pairs]

        mock_file_store = MagicMock()

        def _delete_file(file_id: str) -> None:
            if file_id == failing_file_id:
                raise RuntimeError("simulated S3 outage")

        mock_file_store.delete_file.side_effect = _delete_file

        try:
            with (
                patch(
                    "onyx.server.manage.administrative.client_app.send_task"
                ),
                patch(
                    "onyx.server.manage.administrative.get_default_file_store",
                    return_value=mock_file_store,
                ),
            ):
                response = bulk_create_deletion_attempt(
                    bulk_request=BulkDeletionRequest(source=source),
                    user=admin,
                    db_session=db_session,
                )

            results = _results_by_id(response, {p.id for p in all_pairs})

            failing_result = results[failing_pair.id]
            assert failing_result.outcome == BulkActionOutcome.WARNING
            assert failing_result.message is not None
            assert "simulated s3 outage" in failing_result.message.lower()

            for sibling in sibling_pairs:
                assert results[sibling.id].outcome == BulkActionOutcome.SUCCEEDED

            # The status commit happens BEFORE file cleanup, so the failing
            # connector's deletion is still genuinely in motion.
            for pair in all_pairs:
                assert (
                    _refresh_status(db_session, pair)
                    == ConnectorCredentialPairStatus.DELETING
                )
        finally:
            db_session.rollback()
            for pair in all_pairs:
                cleanup_cc_pair(db_session, pair)

    def test_file_source_bulk_delete_at_scale_exercises_bounded_pool(
        self,
        db_session: Session,
    ) -> None:
        """100+ FILE-source connectors, all with a backing file. No mocked
        failures here — just validates the bounded pool + set-based status
        update both scale correctly."""
        admin = _admin_user(db_session)
        source = DocumentSource.FILE
        num_pairs = 110

        pairs = [
            _create_cc_pair(
                db_session,
                source,
                ConnectorCredentialPairStatus.PAUSED,
                connector_specific_config={
                    "file_locations": [f"file-{uuid4().hex[:8]}"]
                },
            )
            for _ in range(num_pairs)
        ]

        mock_file_store = MagicMock()

        try:
            with (
                patch(
                    "onyx.server.manage.administrative.client_app.send_task"
                ),
                patch(
                    "onyx.server.manage.administrative.get_default_file_store",
                    return_value=mock_file_store,
                ),
            ):
                response = bulk_create_deletion_attempt(
                    bulk_request=BulkDeletionRequest(source=source),
                    user=admin,
                    db_session=db_session,
                )

            results = _results_by_id(response, {p.id for p in pairs})
            assert len(results) == num_pairs
            assert all(
                result.outcome == BulkActionOutcome.SUCCEEDED
                for result in results.values()
            )
            for pair in pairs:
                assert (
                    _refresh_status(db_session, pair)
                    == ConnectorCredentialPairStatus.DELETING
                )
        finally:
            db_session.rollback()
            for pair in pairs:
                cleanup_cc_pair(db_session, pair)


# ---------------------------------------------------------------------------
# Bulk status: idempotency sanity check (SKIPPED, not FAILED, on a re-run)
# ---------------------------------------------------------------------------


class TestBulkStatusIdempotency:
    def test_pausing_an_already_paused_connector_is_skipped(
        self,
        db_session: Session,
    ) -> None:
        admin = _admin_user(db_session)
        source = DocumentSource.MOCK_CONNECTOR

        pair = _create_cc_pair(
            db_session, source, ConnectorCredentialPairStatus.PAUSED
        )
        try:
            with patch(
                "onyx.server.documents.cc_pair.client_app.send_task"
            ):
                response = bulk_update_cc_pair_status(
                    bulk_request=BulkCCStatusUpdateRequest(
                        source=source,
                        status=ConnectorCredentialPairStatus.PAUSED,
                    ),
                    user=admin,
                    db_session=db_session,
                )

            results = _results_by_id(response, {pair.id})
            assert results[pair.id].outcome == BulkActionOutcome.SKIPPED
        finally:
            db_session.rollback()
            cleanup_cc_pair(db_session, pair)
