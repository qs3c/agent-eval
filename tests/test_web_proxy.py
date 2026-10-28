import asyncio

import pytest

from agent_harness_eval.web_proxy import bridge_proxy, device_proxy, proxy_env


def test_device_proxy_precedence_and_validation(monkeypatch):
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    assert device_proxy() == (None, None)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    assert device_proxy() == ("http://127.0.0.1:7890", "HTTP_PROXY")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7891")
    assert device_proxy() == ("http://127.0.0.1:7891", "https_proxy")
    monkeypatch.setenv("https_proxy", "socks5://127.0.0.1:7890")
    with pytest.raises(ValueError, match="HTTP"):
        device_proxy()


def test_proxy_environment_keeps_model_relay_on_loopback():
    env = proxy_env("http://172.17.0.1:12345")
    assert env["http_proxy"] == env["HTTPS_PROXY"]
    assert "127.0.0.1" in env["NO_PROXY"].split(",")


@pytest.mark.parametrize("allowed", [True, False])
def test_bridge_only_accepts_trial_peer_and_closes_listener(allowed):
    from urllib.parse import urlsplit

    async def check():
        calls = []

        async def upstream(reader, writer):
            calls.append(True)
            writer.write(await reader.read(100))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        try:
            url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with bridge_proxy(url, "127.0.0.1", "127.0.0.1" if allowed else "192.0.2.1") as endpoint:
                port = urlsplit(endpoint).port
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                if allowed:
                    writer.write(b"fixture proxy bytes")
                    await writer.drain()
                    assert await asyncio.wait_for(reader.read(100), 2) == b"fixture proxy bytes"
                else:
                    assert await asyncio.wait_for(reader.read(100), 2) == b""
                writer.close()
                await writer.wait_closed()
            assert bool(calls) == allowed
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(check())
