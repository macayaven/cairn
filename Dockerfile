# cairn in one image: the web UI, the REST API and the MCP endpoint on port 8765, with the
# database on the /data volume. Runs as a non-root user (uid 10001).
#
#   docker run -d --name cairn -p 127.0.0.1:8765:8765 -v cairn-data:/data IMAGE
#
# Base image: Docker's official python:3.13-slim rather than NGC. Exception recorded on
# 2026-11-10: NGC has nvcr.io/nvidia/distroless/python (3.13-v4.2.1, amd64 and arm64), but
# cairn uses no GPU, CUDA or driver feature, this image is published for any Docker host
# rather than for one machine, and that NGC image states no licence in its metadata, which
# leaves redistributing it inside a public image unclear. The official image is PSF and
# Debian licensed.

# Both are pinned by the digest of their multi-platform index (amd64 and arm64). To move
# either: change the tag, look up the new index digest (docker buildx imagetools inspect
# IMAGE:TAG) and replace it here. The final stage upgrades the base image's Debian packages
# from Debian's archive (its security fixes reach the archive before a new index of the base
# image), so two builds of the same commit can differ in those packages.
ARG PYTHON=python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ARG UV=ghcr.io/astral-sh/uv:0.11.19@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6

FROM ${UV} AS uv

FROM ${PYTHON} AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app
WORKDIR /src
# Dependencies first, so a change to cairn's own source reuses this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md ./
# The licence goes in before the build that installs the project, which reads it into the
# package's metadata. It comes after the dependency layer, so a change to it does not rebuild
# that layer.
COPY LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON}
# The base image's Debian packages, up to the fixed versions Debian has published; and no
# package installer: cairn installs nothing at run time, and the base image's pip carries
# copies of other packages (pip/_vendor) that a scan reports as the image's own.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes \
 && rm -rf /var/lib/apt/lists/*
RUN python -m pip uninstall --yes pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip/_bundled
RUN groupadd --system --gid 10001 cairn \
 && useradd --system --uid 10001 --gid cairn --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin cairn \
 && install -d -o cairn -g cairn -m 0700 /data
COPY --from=build /app /app
# A plain copy of the notice beside the application (/app holds bin/ and lib/).
COPY --from=build /src/LICENSE /app/LICENSE
ENV PATH=/app/bin:$PATH \
    PYTHONUNBUFFERED=1
USER cairn
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765
# The probe imports only `socket` and skips site packages, so it starts in a fraction of the
# time an HTTP client import takes: on a small or emulated host that import alone can use up
# the probe's timeout and mark a serving container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-I", "-S", "-c", "import socket; s = socket.create_connection(('127.0.0.1', 8765), 3); s.sendall(b'GET /healthz HTTP/1.0\\r\\nHost: 127.0.0.1\\r\\n\\r\\n'); raise SystemExit(0 if b' 200 ' in s.recv(64) else 1)"]
ENTRYPOINT ["python", "-m", "cairn.server"]
CMD ["serve", "--db", "/data/cairn.db", "--host", "0.0.0.0"]
