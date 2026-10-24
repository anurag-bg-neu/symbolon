import pytest

from tests.helpers import Lab


@pytest.fixture(scope="session")
def lab() -> Lab:
    """Fixture lab key and Ed25519 seed, fresh per test run."""
    return Lab()


@pytest.fixture
def other_lab() -> Lab:
    return Lab()
