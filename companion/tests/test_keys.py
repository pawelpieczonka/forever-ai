import os
import threading

import pytest

from foreverai import config, keystore

from test_gemini import FakeGemini
from test_service import env  # noqa: F401  (fixture)

PRIVATE = os.name != "nt"  # file modes exist only there


@pytest.fixture
def secure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "secure_keys", lambda: True)
    monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
    for var in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / ".env"


def test_keys_go_to_the_credential_store_and_old_env_keys_move_there(secure, memory_keyring):
    secure.write_text("OPENROUTER_API_KEY=sk-or-old\nOTHER=1\n")
    assert config.read_key("openrouter") == "sk-or-old"
    assert memory_keyring.items == {("ForeverAI", "OPENROUTER_API_KEY"): "sk-or-old"}
    assert secure.read_text() == "OTHER=1\n"  # the plain copy is gone, other lines stay
    if PRIVATE:
        assert secure.stat().st_mode & 0o777 == 0o600
    assert config.write_key("gemini", " AIza-new ") is True
    assert memory_keyring.items[("ForeverAI", "GEMINI_API_KEY")] == "AIza-new"
    assert "GEMINI" not in secure.read_text()
    assert config.read_key("gemini") == "AIza-new" and config.read_key("openrouter") == "sk-or-old"
    calls = memory_keyring.calls
    assert config.read_key("gemini") == "AIza-new" and memory_keyring.calls == calls  # read once, then from memory
    secure.write_text("GEMINI_API_KEY=AIza-plain\n")
    config.write_key("gemini", "AIza-newer")
    assert not secure.exists()  # nothing left in it: removed


def test_without_a_credential_store_keys_stay_in_a_private_file(secure, memory_keyring, monkeypatch):
    def no_store():
        raise RuntimeError("The Secret Service daemon is neither running nor activatable through D-Bus")
    monkeypatch.setattr(keystore, "_load", no_store)
    keystore.reset()
    assert config.write_key("gemini", "AIza-in-file") is False
    assert secure.read_text() == "GEMINI_API_KEY=AIza-in-file\n"
    if PRIVATE:
        assert secure.stat().st_mode & 0o777 == 0o600
    assert config.read_key("gemini") == "AIza-in-file"
    # the next start finds a store: the key in the file is the newer one and moves there
    memory_keyring.items[("ForeverAI", "GEMINI_API_KEY")] = "AIza-older"
    monkeypatch.setattr(keystore, "_load", lambda: memory_keyring)
    keystore.reset()
    assert config.read_key("gemini") == "AIza-in-file"
    assert memory_keyring.items[("ForeverAI", "GEMINI_API_KEY")] == "AIza-in-file" and not secure.exists()


def test_a_store_that_fails_is_not_asked_again(secure, memory_keyring):
    def locked(*a):
        memory_keyring.calls += 1
        raise RuntimeError("Failed to unlock the collection!")  # the player dismissed the unlock prompt
    memory_keyring.get_password = locked
    assert config.read_key("gemini") is None
    assert config.write_key("gemini", "AIza-x") is False and config.read_key("gemini") == "AIza-x"
    assert memory_keyring.calls == 1


def test_save_key_says_where_the_key_is(env, monkeypatch, memory_keyring):  # noqa: F811
    svc, _, tmp = env
    monkeypatch.setattr(config, "secure_keys", lambda: True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    svc.gemini_factory = FakeGemini
    res = {}
    t = threading.Thread(target=lambda: res.update(r=svc.save_key("gemini", "AIzaSyGOOD000000000000000000000",
                                                                    use=True)))
    t.start()
    t.join(5)
    assert res["r"] == (True, f"Key saved in {keystore.NAME}. Using Google Gemini.")
    assert "GEMINI" not in (tmp / ".env").read_text()
    assert memory_keyring.items[("ForeverAI", "GEMINI_API_KEY")] == "AIzaSyGOOD000000000000000000000"
