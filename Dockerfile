# syntax=docker/dockerfile:1
#
# Optional container packaging. The default way to run this server is the
# console script installed with `uv tool install .`; this image exists for
# isolation and for hosts that prefer containers.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MANAPOOL_TRANSPORT=stdio \
    MANAPOOL_CACHE_DIR=/cache

WORKDIR /app

# Dependencies are pinned in constraints.txt and installed before the source is
# copied, so editing the code does not reinstall pydantic, cryptography, etc.
COPY pyproject.toml README.md constraints.txt ./
RUN pip install -c constraints.txt mcp httpx selectolax hatchling

COPY src ./src
RUN pip install --no-build-isolation --no-deps . \
    && pip check \
    && pip uninstall -y hatchling \
    && useradd --create-home --uid 10001 mcp \
    && mkdir -p /cache \
    && chown mcp:mcp /cache

USER mcp

# Price snapshot lives here. Mount a volume to keep the 53 MB download across
# container runs: docker run -v manapool-cache:/cache ...
VOLUME ["/cache"]

EXPOSE 8765

# stdio transport: run with `docker run -i --rm`, never `-t`.
ENTRYPOINT ["manapool-mcp"]
