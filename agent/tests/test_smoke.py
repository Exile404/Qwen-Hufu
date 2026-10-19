import pytest
from fastapi.testclient import TestClient

from hufu import llm
from hufu.api import app
from hufu.config import get_settings


def test_settings_load():
    s = get_settings()
    assert s.llm_base_url.startswith("http")
    assert s.chain_id > 0


@pytest.mark.integration
@pytest.mark.llm
def test_llm_emits_tool_call():
    result = llm.probe()
    assert result["ok"], result


@pytest.mark.integration
@pytest.mark.llm
def test_health_endpoint():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["ok"], body
    assert body["chain"]["ok"], body
