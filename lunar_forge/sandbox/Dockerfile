FROM python:3.12-bookworm

RUN apt-get update && apt-get install -y \
    bash \
    git \
    curl \
    ripgrep \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
