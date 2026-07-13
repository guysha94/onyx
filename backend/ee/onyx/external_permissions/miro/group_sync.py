"""Miro external group sync (FORK: miro).

Builds one Onyx external group per Miro team (`miro_team_<team_id>`), whose
members are that team's member emails. `miro_doc_sync` tags each asset with its
board's team group, so a user can see a Miro asset iff they belong to the
board's team (matched by email).
"""
from collections.abc import Generator

from ee.onyx.db.external_perm import ExternalUserGroup
from onyx.connectors.miro.connector import MiroConnector
from onyx.connectors.miro.connector import team_group_id
from onyx.db.models import ConnectorCredentialPair
from onyx.utils.logger import setup_logger

logger = setup_logger()


def miro_group_sync(
    tenant_id: str,  # noqa: ARG001
    cc_pair: ConnectorCredentialPair,
) -> Generator[ExternalUserGroup, None, None]:
    miro_connector = MiroConnector(
        **cc_pair.connector.connector_specific_config,
    )
    credential_json = (
        cc_pair.credential.credential_json.get_value(apply_mask=False)
        if cc_pair.credential.credential_json
        else {}
    )
    miro_connector.load_credentials(credential_json)

    team_to_member_emails = miro_connector.get_team_member_email_map()

    for team_id, member_emails in team_to_member_emails.items():
        yield ExternalUserGroup(
            id=team_group_id(team_id),
            user_emails=list(member_emails),
        )
