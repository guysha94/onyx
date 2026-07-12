"""Miro document permission sync (FORK: miro).

Tags each Miro asset with its board's `ExternalAccess` (team external group /
public / admin-only) via the shared `generic_doc_sync` helper. Member emails
for the team groups are resolved separately by `miro_group_sync`.
"""
from collections.abc import Generator

from ee.onyx.external_permissions.perm_sync_types import FetchAllDocumentsFunction
from ee.onyx.external_permissions.perm_sync_types import FetchAllDocumentsIdsFunction
from ee.onyx.external_permissions.utils import generic_doc_sync
from onyx.access.models import ElementExternalAccess
from onyx.configs.constants import DocumentSource
from onyx.connectors.miro.connector import MiroConnector
from onyx.db.models import ConnectorCredentialPair
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.utils.logger import setup_logger

logger = setup_logger()

MIRO_DOC_SYNC_TAG = "miro_doc_sync"


def miro_doc_sync(
    cc_pair: ConnectorCredentialPair,
    fetch_all_existing_docs_fn: FetchAllDocumentsFunction,  # noqa: ARG001
    fetch_all_existing_docs_ids_fn: FetchAllDocumentsIdsFunction,
    callback: IndexingHeartbeatInterface | None = None,
) -> Generator[ElementExternalAccess, None, None]:
    miro_connector = MiroConnector(
        **cc_pair.connector.connector_specific_config,
    )
    credential_json = (
        cc_pair.credential.credential_json.get_value(apply_mask=False)
        if cc_pair.credential.credential_json
        else {}
    )
    miro_connector.load_credentials(credential_json)

    yield from generic_doc_sync(
        cc_pair=cc_pair,
        fetch_all_existing_docs_ids_fn=fetch_all_existing_docs_ids_fn,
        callback=callback,
        doc_source=DocumentSource.MIRO,
        slim_connector=miro_connector,
        label=MIRO_DOC_SYNC_TAG,
    )
