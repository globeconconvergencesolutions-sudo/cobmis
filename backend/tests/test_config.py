import pytest
from pydantic import ValidationError

from app.core.config import Settings, load_profile


@pytest.mark.parametrize("name", ["small", "medium", "large"])
def test_profiles_are_complete(name):
    prof = load_profile(name)
    s = Settings(_env_file=None, profile=name)
    for key, value in prof.items():
        assert getattr(s, key) == value


def test_env_override_beats_profile(monkeypatch):
    monkeypatch.setenv("COB_CHAT_MODEL", "llama3.1:8b")
    s = Settings(_env_file=None, profile="small")
    assert s.chat_model == "llama3.1:8b"
    assert s.embed_model == load_profile("small")["embed_model"]


def test_unknown_profile_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, profile="huge")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ollama_url", "http://0.0.0.0:11434"),
        ("ollama_url", "http://api.example.com"),
        ("db_host", "10.0.0.5"),
        ("api_host", "0.0.0.0"),
    ],
)
def test_non_loopback_endpoints_rejected(field, value):
    with pytest.raises(ValidationError, match="Non-loopback"):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
def test_loopback_accepted(url):
    Settings(_env_file=None, ollama_url=url)
