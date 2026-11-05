# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MANAPOOL_TRANSPORT=stdio

WORKDIR /app

# Dependencies are pinned in constraints.txt and installed before the source is
# copied, so editing the code does not reinstall pydantic, cryptography, etc.
COPY pyproject.toml README.md constraints.txt ./
RUN pip install -c constraints.txt mcp httpx selectolax hatchling

COPY src ./src
RUN pip install --no-build-isolation --no-deps . \
    && pip check \
    && pip uninstall -y hatchling \
    && useradd --create-home --uid 10001 mcp

USER mcp

EXPOSE 8765

# stdio transport: run with `docker run -i --rm`, never `-t`.
ENTRYPOINT ["manapool-mcp"]
