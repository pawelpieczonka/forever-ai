import pytest

from foreverai import keystore


class MemoryKeyring:
    """Stands in for the system credential store: tests never touch the real one."""

    def __init__(self):
        self.items = {}
        self.calls = 0

    def get_password(self, service, name):
        self.calls += 1
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.calls += 1
        self.items[(service, name)] = value


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    kr = MemoryKeyring()
    monkeypatch.setattr(keystore, "_load", lambda: kr)
    keystore.reset()
    yield kr
    keystore.reset()


@pytest.fixture(autouse=True)
def no_zone_quests_for_routes(monkeypatch):
    """A route is planned with the quests of its zones read from the game; in tests no game answers, and each route
    would wait for it. The tests of that reading turn it on themselves."""
    from foreverai.service import Service
    monkeypatch.setattr(Service, "fetch_route_quests", False)


@pytest.fixture(autouse=True)
def no_language_naming(monkeypatch):
    """The language of a conversation is named by a model call of its own: off in the tests, which count the calls."""
    from foreverai.service import Service
    monkeypatch.setattr(Service, "name_language", False)
