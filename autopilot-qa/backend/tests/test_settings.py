import pytest
from pydantic import ValidationError

from settings import Settings


def test_defaults_match_spec(monkeypatch):
    monkeypatch.delenv("HEALING_CONFIDENCE_THRESHOLD", raising=False)
    s = Settings(_env_file=None)
    assert s.mongo_uri == "mongodb://localhost:27017"
    assert s.mongo_db == "autopilot_qa"
    assert s.ollama_url == "http://localhost:11434"
    assert s.healing_confidence_threshold == 0.80
    assert s.service_urls() == {
        "auth": "http://localhost:4001",
        "catalog": "http://localhost:4002",
        "cart": "http://localhost:4003",
        "order": "http://localhost:4004",
        "payment": "http://localhost:4005",
    }


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("HEALING_CONFIDENCE_THRESHOLD", "0.9")
    monkeypatch.setenv("CATALOG_SERVICE_URL", "http://localhost:9002")
    s = Settings(_env_file=None)
    assert s.healing_confidence_threshold == 0.9
    assert s.service_urls()["catalog"] == "http://localhost:9002"


def test_threshold_must_be_a_probability(monkeypatch):
    monkeypatch.setenv("HEALING_CONFIDENCE_THRESHOLD", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
