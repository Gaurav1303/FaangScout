"""Shared pytest fixtures: mocked HTTP transport so tests never touch the network."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def mock_client_factory():
    """Returns a factory: given a handler(request) -> Response, builds an httpx.Client."""

    def _make(handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return _make
