"""Conformité à specs/001-agent-harness-platform/contracts/openapi.yaml (openapi-core)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse

SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "001-agent-harness-platform"
    / "contracts"
    / "openapi.yaml"
)
API_PREFIX = "/api/v1"


@pytest.fixture(scope="session")
def openapi_spec() -> OpenAPI:
    return OpenAPI.from_file_path(str(SPEC_PATH))


@pytest.fixture
def assert_matches_contract(openapi_spec: OpenAPI) -> Callable[[httpx.Response], None]:
    """Valide requête et réponse d'un appel `httpx` contre le contrat (statut, schéma, type)."""

    def _assert(response: httpx.Response) -> None:
        request = response.request
        parts = urlsplit(str(request.url))
        path = parts.path
        assert path.startswith(API_PREFIX), f"chemin hors contrat : {path}"
        mock_request = MockRequest(
            f"{parts.scheme}://{parts.netloc}{API_PREFIX}",
            request.method.lower(),
            path[len(API_PREFIX) :],
            args=dict(httpx.QueryParams(parts.query)),
            headers=dict(request.headers),
            cookies=dict(request.headers.get("cookie", "") and {}),
            data=request.content or None,
            content_type=request.headers.get("content-type", "application/json").split(";")[0],
        )
        mock_response = MockResponse(
            response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            content_type=response.headers.get("content-type", "application/json").split(";")[0],
        )
        openapi_spec.validate_response(mock_request, mock_response)

    return _assert
