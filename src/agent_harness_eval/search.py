"""Explicit, non-secret BKCrab search configuration and its egress hosts."""

import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .config import Strict


class WebSearch(Strict):
    provider: Literal["exa", "brave", "searxng"]
    transport: Literal["direct", "platform-relay"] = "direct"
    endpoint: str = ""
    credential_env: str = Field(default="", pattern=r"^([A-Za-z_][A-Za-z0-9_]*)?$")

    @model_validator(mode="after")
    def validate_backend(self):
        if self.transport == "platform-relay" and self.provider != "exa":
            raise ValueError("platform-relay currently supports Exa only")
        if self.provider == "searxng":
            url = urlsplit(self.endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or any(c.isspace() for c in self.endpoint)
                or self.credential_env
            ):
                raise ValueError("SearxNG requires an HTTP(S) endpoint without credentials, query or fragment")
            # Validate the port as well, before freezing an experiment.
            _ = url.port
        elif not self.credential_env or self.endpoint:
            raise ValueError("Exa/Brave require credential_env and use their built-in endpoint")
        return self

    @property
    def host(self):
        return {"exa": "api.exa.ai", "brave": "api.search.brave.com"}.get(
            self.provider, urlsplit(self.endpoint).hostname
        )


def search_hosts(exp):
    hosts = {
        WebSearch.model_validate(a.kwargs["web_search"]).host
        for a in exp.agents if a.kwargs.get("web_search") is not None
    }
    if exp.model.search_review:
        hosts.add(urlsplit(exp.model.search_review.endpoint).hostname)
    return sorted(hosts)


def validate_search_credentials(exp):
    for agent in exp.agents:
        if agent.kwargs.get("web_search") is not None:
            search = WebSearch.model_validate(agent.kwargs["web_search"])
            if search.credential_env and not os.environ.get(search.credential_env, "").strip():
                raise ValueError("Missing web search credential environment variable: " + search.credential_env)
