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
