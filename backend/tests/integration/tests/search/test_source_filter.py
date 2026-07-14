"""Integration tests for server-side source filtering on the Onyx Search UI
backend (POST /api/search/send-search-message).

Covers the multi-select source filter feature: default (no filter) returns
results from all sources, an explicit filter scopes results to the selected
source(s), and clearing the filter (source_type=None) returns to all-sources
behavior.
"""

from __future__ import annotations

import httpx

from onyx.configs.constants import DocumentSource
from tests.integration.common_utils.constants import API_SERVER_URL
from tests.integration.common_utils.http_client import client
from tests.integration.common_utils.managers.cc_pair import CCPairManager
from tests.integration.common_utils.managers.document import DocumentManager
from tests.integration.common_utils.test_models import DATestAPIKey
from tests.integration.common_utils.test_models import DATestLLMProvider
from tests.integration.common_utils.test_models import DATestUser

SEARCH_URL = f"{API_SERVER_URL}/search/send-search-message"


def _search(
    query: str,
    user: DATestUser,
    source_type: list[str] | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "search_query": query,
        "include_content": True,
        "stream": False,
    }
    if source_type is not None:
        body["filters"] = {"source_type": source_type}
    return client.post(SEARCH_URL, json=body, headers=user.headers)


def _contents(resp: httpx.Response) -> list[str]:
    return [doc["content"] for doc in resp.json()["search_docs"]]


def test_no_filter_returns_all_sources(
    admin_user: DATestUser,
    llm_provider: DATestLLMProvider,  # noqa: ARG001
    api_key: DATestAPIKey,
) -> None:
    cc_pair = CCPairManager.create_from_scratch(user_performing_action=admin_user)

    shared_phrase = "source-filter-default-unique-phrase"
    jira_content = f"{shared_phrase} from jira"
    github_content = f"{shared_phrase} from github"
    DocumentManager.seed_doc_with_content(
        cc_pair, jira_content, api_key, source=DocumentSource.JIRA
    )
    DocumentManager.seed_doc_with_content(
        cc_pair, github_content, api_key, source=DocumentSource.GITHUB
    )

    resp = _search(shared_phrase, admin_user)
    assert resp.status_code == 200

    contents = _contents(resp)
    assert any(jira_content in c for c in contents)
    assert any(github_content in c for c in contents)


def test_single_source_filter_scopes_results(
    admin_user: DATestUser,
    llm_provider: DATestLLMProvider,  # noqa: ARG001
    api_key: DATestAPIKey,
) -> None:
    cc_pair = CCPairManager.create_from_scratch(user_performing_action=admin_user)

    shared_phrase = "source-filter-single-unique-phrase"
    jira_content = f"{shared_phrase} from jira"
    github_content = f"{shared_phrase} from github"
    DocumentManager.seed_doc_with_content(
        cc_pair, jira_content, api_key, source=DocumentSource.JIRA
    )
    DocumentManager.seed_doc_with_content(
        cc_pair, github_content, api_key, source=DocumentSource.GITHUB
    )

    resp = _search(shared_phrase, admin_user, source_type=[DocumentSource.JIRA.value])
    assert resp.status_code == 200

    contents = _contents(resp)
    assert any(jira_content in c for c in contents)
    assert not any(github_content in c for c in contents)


def test_multi_source_filter_scopes_results(
    admin_user: DATestUser,
    llm_provider: DATestLLMProvider,  # noqa: ARG001
    api_key: DATestAPIKey,
) -> None:
    cc_pair = CCPairManager.create_from_scratch(user_performing_action=admin_user)

    shared_phrase = "source-filter-multi-unique-phrase"
    jira_content = f"{shared_phrase} from jira"
    github_content = f"{shared_phrase} from github"
    confluence_content = f"{shared_phrase} from confluence"
    DocumentManager.seed_doc_with_content(
        cc_pair, jira_content, api_key, source=DocumentSource.JIRA
    )
    DocumentManager.seed_doc_with_content(
        cc_pair, github_content, api_key, source=DocumentSource.GITHUB
    )
    DocumentManager.seed_doc_with_content(
        cc_pair, confluence_content, api_key, source=DocumentSource.CONFLUENCE
    )

    resp = _search(
        shared_phrase,
        admin_user,
        source_type=[DocumentSource.JIRA.value, DocumentSource.CONFLUENCE.value],
    )
    assert resp.status_code == 200

    contents = _contents(resp)
    assert any(jira_content in c for c in contents)
    assert any(confluence_content in c for c in contents)
    assert not any(github_content in c for c in contents)


def test_clearing_filter_returns_to_all_sources(
    admin_user: DATestUser,
    llm_provider: DATestLLMProvider,  # noqa: ARG001
    api_key: DATestAPIKey,
) -> None:
    cc_pair = CCPairManager.create_from_scratch(user_performing_action=admin_user)

    shared_phrase = "source-filter-clear-unique-phrase"
    jira_content = f"{shared_phrase} from jira"
    github_content = f"{shared_phrase} from github"
    DocumentManager.seed_doc_with_content(
        cc_pair, jira_content, api_key, source=DocumentSource.JIRA
    )
    DocumentManager.seed_doc_with_content(
        cc_pair, github_content, api_key, source=DocumentSource.GITHUB
    )

    # Scope to Jira only.
    scoped_resp = _search(
        shared_phrase, admin_user, source_type=[DocumentSource.JIRA.value]
    )
    assert scoped_resp.status_code == 200
    scoped_contents = _contents(scoped_resp)
    assert any(jira_content in c for c in scoped_contents)
    assert not any(github_content in c for c in scoped_contents)

    # Explicitly clear the filter (source_type=None) — must return to
    # all-sources behavior, not silently keep the prior scope.
    cleared_resp = _search(shared_phrase, admin_user, source_type=None)
    assert cleared_resp.status_code == 200
    cleared_contents = _contents(cleared_resp)
    assert any(jira_content in c for c in cleared_contents)
    assert any(github_content in c for c in cleared_contents)
