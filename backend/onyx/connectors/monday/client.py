"""
Thin adapter around the monday.com Python SDK for connector use.

Discovery helpers use SDK resource methods; rich/custom GraphQL (items, ACL,
slim subscribers) goes through run_query with proper variable support and
retries.
"""

import json
from typing import Any
from typing import cast

import urllib3
from monday import MondayClient
from monday.constants import TOKEN_HEADER

from onyx.connectors.exceptions import CredentialExpiredError
from onyx.connectors.exceptions import InsufficientPermissionsError
from onyx.utils.logger import setup_logger

logger = setup_logger()

_NUM_RETRIES = 5
_DEFAULT_TIMEOUT = 60
_MONDAY_GRAPHQL_URL = "https://api.monday.com/v2"
_DEFAULT_API_VERSION = "2025-10"


class MondayApiClient:
    def __init__(
        self,
        token: str,
        *,
        api_version: str = _DEFAULT_API_VERSION,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._headers = {
            "API-Version": api_version,
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._http = urllib3.PoolManager()
        self._sdk = MondayClient(
            token,
            headers=self._headers.copy(),
            timeout=timeout,
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        response = self._sdk.workspaces.get_workspaces()
        return cast(
            list[dict[str, Any]], response.get("data", {}).get("workspaces", [])
        )

    def list_boards_page(
        self,
        *,
        limit: int,
        page: int,
        workspace_ids: list[int] | None = None,
        ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        response = self._sdk.boards.fetch_boards(
            limit=limit,
            page=page,
            workspace_ids=workspace_ids,
            ids=ids,
        )
        return cast(list[dict[str, Any]], response.get("data", {}).get("boards", []))

    def run_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(_NUM_RETRIES):
            try:
                return self._execute(query, variables)
            except (CredentialExpiredError, InsufficientPermissionsError):
                raise
            except Exception as exc:
                if attempt == _NUM_RETRIES - 1:
                    raise exc
                logger.warning(
                    "A monday.com GraphQL error occurred: %s. Retrying...", exc
                )

        raise RuntimeError(
            "Unexpected execution when querying monday.com. This should never happen."
        )

    def _execute(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = self._headers.copy()
        headers[TOKEN_HEADER] = self._token

        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables

        response = self._http.request(
            "POST",
            _MONDAY_GRAPHQL_URL,
            headers=headers,
            body=json.dumps(body).encode("utf-8"),
            timeout=self._timeout,
        )

        if response.status == 401:
            raise CredentialExpiredError("Invalid monday.com API token (HTTP 401).")
        if response.status == 403:
            raise InsufficientPermissionsError(
                "Insufficient permissions for monday.com API (HTTP 403)."
            )
        if response.status >= 400:
            raise RuntimeError(
                f"Error querying monday.com API (status={response.status}): "
                f"{response.data.decode('utf-8')}"
            )

        response_json = json.loads(response.data.decode("utf-8"))
        if errors := response_json.get("errors"):
            error_messages = "; ".join(
                str(error.get("message", error)) for error in errors
            )
            raise RuntimeError(f"monday.com GraphQL error: {error_messages}")

        data = response_json.get("data")
        if data is None:
            raise RuntimeError(
                f"monday.com GraphQL response missing data: {response_json}"
            )
        return cast(dict[str, Any], data)
