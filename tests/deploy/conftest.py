"""Fixtures for the deploy tests; the stubs themselves are in deploy_stubs.py."""

import pytest

from tests.deploy.deploy_stubs import Stubs


@pytest.fixture
def stubs(tmp_path):
    return Stubs(tmp_path)
