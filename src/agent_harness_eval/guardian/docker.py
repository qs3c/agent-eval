import json
import re
from pathlib import Path

from .evidence import now
from .process import command

# Child-side timeout prevents a killed docker client leaving shell grandchildren.
SHELL = r"""
import os,signal,subprocess,sys,json,selectors,time
p=subprocess.Popen(['sh','-c',sys.argv[1]],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
selector=selectors.DefaultSelector()
buffers=[bytearray(),bytearray()]
for i,f in enumerate((p.stdout,p.stderr)): selector.register(f,selectors.EVENT_READ,i)
deadline=time.monotonic()+float(sys.argv[2])
timed=False
truncated=False
try:
 while selector.get_map():
  if time.monotonic()>=deadline:
   timed=True
   break
  for key,_ in selector.select(min(.1,max(0,deadline-time.monotonic()))):
   data=os.read(key.fd,65536)
   if not data: selector.unregister(key.fileobj)
   b=buffers[key.data]
   available=65536-len(b)
   b.extend(data[:available])
   truncated=truncated or len(data)>available
 try: p.wait(timeout=max(0,deadline-time.monotonic()))
 except subprocess.TimeoutExpired: timed=True
finally:
 try: os.killpg(p.pid,signal.SIGKILL)
 except ProcessLookupError: pass
 p.wait()
 selector.close()
print(json.dumps(dict(returncode=p.returncode,timed_out=timed,stdout=buffers[0].decode('utf-8','replace'),stderr=buffers[1].decode('utf-8','replace'),truncated=truncated)))
"""


class DockerTarget:
    def __init__(self, trial_name, timeout=20):
        self.project = re.sub(r"[^a-z0-9_-]", "", (trial_name + "__env").lower())
        self.timeout = timeout
        self.container_id = None
        self.active = lambda: True

    async def inspect(self):
        if not self.container_id:
            found = await command(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--no-trunc",
                    "--filter",
                    "label=com.docker.compose.project=" + self.project,
                    "--filter",
                    "label=com.docker.compose.service=main",
                ],
                self.timeout,
            )
            ids = found["stdout"].split()
            if found["returncode"] or len(ids) != 1:
                raise RuntimeError(
                    "Expected exactly one task container: " + found["stderr"][:500]
                )
            self.container_id = ids[0]
        # Do not retrieve Config.Env (credentials).
        fmt = "{{json .Id}}\n{{json .State}}\n{{json .Config.Labels}}\n{{json .Image}}"
        result = await command(
            ["docker", "inspect", "--format", fmt, self.container_id], self.timeout
        )
        if result["returncode"]:
            raise RuntimeError(result["stderr"][:500])
        cid, state, labels, image = map(json.loads, result["stdout"].splitlines())
        if (
            cid != self.container_id
            or labels.get("com.docker.compose.project") != self.project
            or labels.get("com.docker.compose.service") != "main"
        ):
            raise RuntimeError("Task container identity changed")
        return {"id": cid, "state": state, "image": image, "project": self.project}

    async def snapshot(self):
        info = await self.inspect()
        result = await command(
            [
                "docker",
                "exec",
                self.container_id,
                "python3",
                "-c",
                Path(__file__).with_name("probe.py").read_text(encoding="utf-8"),
            ],
            self.timeout,
            limit=2 * 1024 * 1024,
        )
        value = {"captured_at": now(), "container": info}
        try:
            if result["returncode"] or result["truncated"]:
                raise ValueError("probe incomplete: " + result["stderr"][:500])
            value.update(json.loads(result["stdout"]))
        except ValueError as error:
            value["error"] = str(error)
        return value

    async def exec(self, text, timeout):
        info = await self.inspect()
        if not info["state"]["Running"]:
            raise RuntimeError("Task container is no longer running")
        if not self.active():
            raise RuntimeError("Trial phase ended; guardian authority expired")
        result = await command(
            [
                "docker",
                "exec",
                "--user",
                "0",
                self.container_id,
                "python3",
                "-c",
                SHELL,
                text,
                str(timeout),
            ],
            timeout + 5,
        )
        if result["returncode"]:
            return result
        return json.loads(result["stdout"])

    async def stop(self):
        # Exact project scope also includes Harbor's egress sidecar. Never prune globally.
        results = []
        found = await command(
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + self.project,
            ],
            self.timeout,
        )
        if found["returncode"]:
            return {"resources_released": False, "results": [found]}
        for cid in found["stdout"].split():
            if not re.fullmatch(r"[0-9a-f]{64}", cid):
                raise RuntimeError("Invalid Docker container identity")
            results.append(await command(["docker", "rm", "-f", cid], self.timeout))
        networks = await command(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + self.project,
            ],
            self.timeout,
        )
        results.append(networks)
        if networks["returncode"] == 0:
            for nid in networks["stdout"].split():
                if not re.fullmatch(r"[0-9a-f]+", nid):
                    raise RuntimeError("Invalid Docker network identity")
                results.append(
                    await command(["docker", "network", "rm", nid], self.timeout)
                )
        return {
            "resources_released": all(r["returncode"] == 0 for r in results),
            "results": results,
        }
