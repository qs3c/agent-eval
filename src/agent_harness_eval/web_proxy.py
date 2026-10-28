"""Pass the device HTTP proxy to a local Docker trial without exposing it on LAN."""

import asyncio
import ipaddress
import json
import os
import shlex
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit


def device_proxy():
    for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        if value := os.environ.get(name, "").strip():
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname or url.query or url.fragment:
                raise ValueError(f"{name} must be an HTTP(S) proxy URL")
            _ = url.port
            return value, name
    return None, None


def proxy_env(url):
    return {"HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url,
            "ALL_PROXY": "", "all_proxy": "",
            "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"}


@asynccontextmanager
async def bridge_proxy(upstream, gateway, peer):
    """One trial IP may use this listener; it closes with the agent run."""
    url = urlsplit(upstream)
    if url.scheme != "http":
        raise ValueError("Loopback device proxy must use http:// for Docker forwarding")
    clients = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        clients.add(task)
        remote = None
        pumps = []
        try:
            if writer.get_extra_info("peername")[0] != peer:
                return
            other, remote = await asyncio.wait_for(asyncio.open_connection(url.hostname, url.port or 80), 10)

            async def pump(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()

            pumps = [asyncio.create_task(pump(reader, remote)), asyncio.create_task(pump(other, writer))]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, TimeoutError):
            pass
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (remote, writer):
                if stream:
                    stream.close()
            clients.discard(task)

    server = await asyncio.start_server(handle, gateway, 0)
    try:
        port = server.sockets[0].getsockname()[1]
        auth = url.netloc.rsplit("@", 1)[0] + "@" if "@" in url.netloc else ""
        yield urlunsplit(("http", f"{auth}{gateway}:{port}", "", "", ""))
    finally:
        server.close()
        await server.wait_closed()
        remaining = list(clients)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)


@asynccontextmanager
async def web_proxy_environment(agent, environment):
    if agent.model_config.get("mode") != "live":
        yield {}
        return
    mode = getattr(agent, "web_proxy", "device")
    upstream, source = device_proxy() if mode == "device" else (None, None)
    evidence = {"requested": mode, "route": "device-proxy" if upstream else "direct", "source_env": source}
    if not upstream:
        (agent.logs_dir / "web-proxy.json").write_text(json.dumps(evidence, indent=2))
        yield {name: "" for name in proxy_env("")}
        return
    url = urlsplit(upstream)
    evidence["upstream"] = {"scheme": url.scheme, "host": url.hostname, "port": url.port or 80}
    is_loopback = url.hostname == "localhost"
    try:
        is_loopback |= ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        pass
    if not is_loopback:
        (agent.logs_dir / "web-proxy.json").write_text(json.dumps(evidence, indent=2))
        yield proxy_env(upstream)
        return
    probe = """import json,socket,struct
with open('/proc/net/route') as f:
 rows=[r.split() for r in f.readlines()[1:]]
 gateway=next(socket.inet_ntoa(struct.pack('<I',int(r[2],16))) for r in rows if r[1]=='00000000')
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.connect((gateway,9))
print(json.dumps({'gateway':gateway,'peer':s.getsockname()[0]}))
s.close()
"""
    result = await agent.exec_as_agent(environment, "python3 -c " + shlex.quote(probe))
    addresses = json.loads(result.stdout)
    async with bridge_proxy(upstream, **addresses) as endpoint:
        evidence["bridge"] = {"host": addresses["gateway"], "peer": addresses["peer"], "port": urlsplit(endpoint).port}
        (agent.logs_dir / "web-proxy.json").write_text(json.dumps(evidence, indent=2))
        yield proxy_env(endpoint)
