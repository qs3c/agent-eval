FROM golang:1.25-alpine AS go
FROM python:3.12.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git bash ca-certificates coreutils make gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY --from=go /usr/local/go /usr/local/go
COPY bkcrab-eval /installed-agent/bkcrab-eval
ADD runtime.tar.gz /installed-agent/dsh/
RUN ln -s /installed-agent/dsh/node /usr/local/bin/node && chmod -R a+rX /installed-agent
RUN chmod 755 /installed-agent/bkcrab-eval
ENV PATH="/usr/local/go/bin:${PATH}"
WORKDIR /workspace
