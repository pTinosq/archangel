import pytest

from agent import activity
from agent.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path)


@pytest.fixture(autouse=True)
def quiet():
    """Each test starts with no recent activity."""
    activity._recent.clear()
