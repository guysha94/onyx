"""
Tests for the vendor-row bulk connector actions:
- PUT /manage/admin/cc-pair/bulk-status (pause/resume every cc_pair for a source)
- POST /manage/admin/bulk-deletion-attempt (delete every eligible cc_pair for a source)

Covers: all-success pause/resume, the pause-before-delete guard (REJECTED vs
SUCCEEDED partition), idempotency (re-running a bulk pause/delete doesn't fail),
and 100+ connector scale.
"""

from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor

from onyx.db.enums import ConnectorCredentialPairStatus
from onyx.server.documents.models import BulkActionOutcome
from onyx.server.documents.models import DocumentSource
from tests.integration.common_utils.managers.cc_pair import CCPairManager
from tests.integration.common_utils.test_models import DATestCCPair
from tests.integration.common_utils.test_models import DATestUser

# INGESTION_API cc_pairs never trigger real external fetches (LOAD_STATE input,
# no connector-specific config needed), so bulk pause/resume/delete never race
# against actual indexing/docfetching work.
_TEST_SOURCE = DocumentSource.INGESTION_API


def _create_cc_pairs(
    admin_user: DATestUser, count: int, max_workers: int = 10
) -> list[DATestCCPair]:
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                CCPairManager.create_from_scratch,
                source=_TEST_SOURCE,
                user_performing_action=admin_user,
            )
            for _ in range(count)
        ]
        return [future.result() for future in as_completed(futures)]


def _status_by_id(
    admin_user: DATestUser,
) -> dict[int, ConnectorCredentialPairStatus]:
    return {
        status.cc_pair_id: status.cc_pair_status
        for status in CCPairManager.get_indexing_statuses(admin_user)
    }


def test_bulk_pause_and_resume_all_success(
    reset: None,  # noqa: ARG001
    admin_user: DATestUser,
) -> None:
    cc_pairs = _create_cc_pairs(admin_user, count=5)

    pause_response = CCPairManager.bulk_set_status(
        source=_TEST_SOURCE,
        status=ConnectorCredentialPairStatus.PAUSED,
        user_performing_action=admin_user,
    )
    assert pause_response.total == len(cc_pairs)
    assert pause_response.succeeded == len(cc_pairs)
    assert pause_response.rejected == 0
    assert pause_response.warning == 0
    assert pause_response.failed == 0
    assert all(
        result.outcome == BulkActionOutcome.SUCCEEDED
        for result in pause_response.results
    )

    statuses = _status_by_id(admin_user)
    for cc_pair in cc_pairs:
        assert statuses[cc_pair.id] == ConnectorCredentialPairStatus.PAUSED

    resume_response = CCPairManager.bulk_set_status(
        source=_TEST_SOURCE,
        status=ConnectorCredentialPairStatus.ACTIVE,
        user_performing_action=admin_user,
    )
    assert resume_response.succeeded == len(cc_pairs)
    assert resume_response.failed == 0

    statuses = _status_by_id(admin_user)
    for cc_pair in cc_pairs:
        assert statuses[cc_pair.id] == ConnectorCredentialPairStatus.ACTIVE


def test_bulk_pause_is_idempotent(
    reset: None,  # noqa: ARG001
    admin_user: DATestUser,
) -> None:
    cc_pairs = _create_cc_pairs(admin_user, count=3)

    first = CCPairManager.bulk_set_status(
        source=_TEST_SOURCE,
        status=ConnectorCredentialPairStatus.PAUSED,
        user_performing_action=admin_user,
    )
    assert first.succeeded == len(cc_pairs)
    assert first.failed == 0

    # Re-running against already-paused connectors must not fail the batch.
    second = CCPairManager.bulk_set_status(
        source=_TEST_SOURCE,
        status=ConnectorCredentialPairStatus.PAUSED,
        user_performing_action=admin_user,
    )
    assert second.total == len(cc_pairs)
    assert second.succeeded == 0
    assert second.skipped == len(cc_pairs)
    assert second.failed == 0
    assert all(
        result.outcome == BulkActionOutcome.SKIPPED for result in second.results
    )


def test_bulk_delete_requires_pause_first(
    reset: None,  # noqa: ARG001
    admin_user: DATestUser,
) -> None:
    cc_pairs = _create_cc_pairs(admin_user, count=3)

    # All connectors are freshly created (ACTIVE) - deletion should be rejected,
    # and the connectors must be left completely untouched.
    rejected_response = CCPairManager.bulk_delete(
        source=_TEST_SOURCE,
        user_performing_action=admin_user,
    )
    assert rejected_response.total == len(cc_pairs)
    assert rejected_response.rejected == len(cc_pairs)
    assert rejected_response.succeeded == 0
    for result in rejected_response.results:
        assert result.outcome == BulkActionOutcome.REJECTED
        assert result.message is not None
        assert "paused" in result.message.lower()

    statuses = _status_by_id(admin_user)
    for cc_pair in cc_pairs:
        assert statuses[cc_pair.id] == ConnectorCredentialPairStatus.ACTIVE

    # Pause everything, then deletion should succeed for all of them.
    CCPairManager.bulk_set_status(
        source=_TEST_SOURCE,
        status=ConnectorCredentialPairStatus.PAUSED,
        user_performing_action=admin_user,
    )

    delete_response = CCPairManager.bulk_delete(
        source=_TEST_SOURCE,
        user_performing_action=admin_user,
    )
    assert delete_response.total == len(cc_pairs)
    assert delete_response.succeeded == len(cc_pairs)
    assert delete_response.rejected == 0
    assert delete_response.failed == 0

    statuses = _status_by_id(admin_user)
    for cc_pair in cc_pairs:
        # Immediately after the (synchronous, committed) call, the cc_pair is
        # marked DELETING; the actual removal happens asynchronously.
        assert statuses[cc_pair.id] == ConnectorCredentialPairStatus.DELETING

    # Re-running against already-DELETING connectors must not fail the batch.
    repeat_delete_response = CCPairManager.bulk_delete(
        source=_TEST_SOURCE,
        user_performing_action=admin_user,
    )
    assert repeat_delete_response.skipped == len(cc_pairs)
    assert repeat_delete_response.failed == 0


def test_bulk_pause_at_scale(
    reset: None,  # noqa: ARG001
    admin_user: DATestUser,
) -> None:
    """Validate the set-based bulk path handles 100+ connectors for a single
    vendor without timing out and with fully correct per-connector counts."""
    num_cc_pairs = 120
    cc_pairs = _create_cc_pairs(admin_user, count=num_cc_pairs, max_workers=20)
    assert len(cc_pairs) == num_cc_pairs

    response = CCPairManager.bulk_set_status(
        source=_TEST_SOURCE,
        status=ConnectorCredentialPairStatus.PAUSED,
        user_performing_action=admin_user,
    )
    assert response.total == num_cc_pairs
    assert response.succeeded == num_cc_pairs
    assert response.failed == 0

    statuses = _status_by_id(admin_user)
    assert len(statuses) == num_cc_pairs
    for cc_pair in cc_pairs:
        assert statuses[cc_pair.id] == ConnectorCredentialPairStatus.PAUSED
