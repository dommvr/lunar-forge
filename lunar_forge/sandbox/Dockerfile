FROM python:3.12-slim

WORKDIR /workspace

RUN useradd --create-home --shell /bin/bash agent
USER agent

CMD ["python", "--version"]
