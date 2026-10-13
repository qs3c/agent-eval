"""One disposable Harbor Trial process. Parent owns the ultimate deadline."""

import asyncio
import sys
import time
from pathlib import Path

from harbor.models.trial.config import TrialConfig
from harbor.trial.hooks import TrialEvent
from harbor.trial.trial import Trial

from .docker import DockerTarget
from .evidence import now, read, write


async def run(path):
    config = TrialConfig.model_validate_json(path.read_text())
    root = config.trials_dir / config.trial_name
    if read(root / "retention-policy.json") is not None:
        # Harbor stops the container; parent exports a bounded sanitized workspace.
        config.environment.kwargs["keep_containers"] = True
    state = root / "guardian-state.json"
    write(state, {"phase": "creating", "since": time.monotonic(), "time": now()})
    trial = await Trial.create(config)

    async def hook(event):
        write(
            root / "guardian-result-snapshot.json", event.result.model_dump(mode="json")
        )
        write(
            state,
            {
                "phase": event.event.value,
                "since": time.monotonic(),
                "time": now(),
                "agent_seconds": trial.agent_timeout_sec,
                "verifier_seconds": trial.verifier_timeout_sec,
            },
        )
        if event.event == TrialEvent.AGENT_END:
            # Called before Harbor deletes the environment, including API errors.
            try:
                snapshot = await DockerTarget(config.trial_name, timeout=5).snapshot()
            except Exception as error:  # noqa: BLE001 - preserve evidence and continue cleanup on adapter failure
                snapshot = {"error": str(error), "captured_at": now()}
            write(root / "guardian-agent-end.json", snapshot)

    for event in TrialEvent:
        trial.add_hook(event, hook)
    await trial.run()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1])))
