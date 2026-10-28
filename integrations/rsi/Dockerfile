FROM python@sha256:7dff173021016c8829ae58d5ce112ffc388073ebe67f351aeb6ba2909e35f847
RUN apt-get update && apt-get install -y --no-install-recommends git bash ca-certificates coreutils && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /installed-agent /workspace /output /logs/agent
WORKDIR /workspace
