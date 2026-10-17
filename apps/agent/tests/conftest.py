import pytest

from agent.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path)
