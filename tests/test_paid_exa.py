import io
import json
from urllib.request import Request, urlopen

import pytest
from agent_harness_eval.config import Experiment
from agent_harness_eval.resources import live_model
from agent_harness_eval.search import WebSearch
from agent_harness_eval.agents.frozen_cli import Hermes
from test_search_review import payload, proxy


def test_dsh_search_uses_exa_without_auxiliary_model(tmp_path, monkeypatch):
    calls = []
    with proxy(tmp_path, monkeypatch, None) as (base, _):
        # Set the same explicit route installed by Native.model_setup.
        # proxy helper exposes its server only through the request handler.
        original = live_model.Handler.do_POST
        def routed(self):
            self.server.model_config["web_search"] = {"provider": "exa", "transport": "platform-relay"}
            original(self)
        monkeypatch.setattr(live_model.Handler, "do_POST", routed)
        class Response(io.BytesIO):
            status = 200
        def upstream(request, timeout):
            calls.append((request.full_url, json.loads(request.data), request.get_header("X-api-key")))
            return Response(json.dumps({"results": [{"url": "https://docs.python.org", "title": "Python", "highlights": ["pathlib docs"]}]}).encode())
        monkeypatch.setattr(live_model, "urlopen", upstream)
        result = json.load(urlopen(Request(base + "/search/v1/messages", json.dumps(payload()).encode())))
    assert calls == [("https://api.exa.ai/search", {"query": "Django OuterRef API documentation", "type": "auto", "contents": {"highlights": True}, "numResults": 5}, "exa-secret")]
    assert result["content"][0]["content"][0]["url"] == "https://docs.python.org"
    assert result["content"][1]["citations"][0]["cited_text"] == "pathlib docs"
    evidence = json.loads((tmp_path / "search_responses.jsonl").read_text())
    assert evidence["protocol"] == "dsh_messages_to_exa" and evidence["usage"] is None
    assert all("exa-secret" not in p.read_text() for p in tmp_path.glob("*.jsonl"))


def test_hermes_paid_tier_disables_keyless_selection():
    obj = object.__new__(Hermes)
    obj.model_config = {"name": "fixture", "context_window": 1000}
    obj.web_search = WebSearch(provider="exa", credential_env="EXA_API_KEY")
    assert obj.configuration()["web"] == {"backend": "exa", "provider_tier": {"exa": "paid"}, "keyless_fallback": False, "keyless_rescue": False}


@pytest.mark.parametrize("implementation,transport", [
    ("agent_harness_eval.agents.dsh:DeepSeekStandard", "platform-relay"),
    ("agent_harness_eval.agents.frozen_cli:OpenCode", "direct"),
    ("agent_harness_eval.agents.frozen_cli:Hermes", "direct"),
])
def test_adapter_search_transport_is_explicit(implementation, transport):
    data = json.load(open("experiments/swe-live-80-assist.json"))
    agent = data["agents"][0]
    agent["implementation"] = implementation
    agent["kwargs"]["web_search"] = {"provider": "exa", "credential_env": "EXA_API_KEY", "transport": transport}
    Experiment.model_validate(data)
    agent["kwargs"]["web_search"]["transport"] = "direct" if transport == "platform-relay" else "platform-relay"
    with pytest.raises(ValueError, match="requires Exa"):
        Experiment.model_validate(data)
