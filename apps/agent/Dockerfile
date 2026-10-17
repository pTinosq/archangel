# Archangel in the cloud: the agent, serving the built panel. Built from the repo root, since it needs both apps.

FROM node:24-slim AS panel
WORKDIR /app/apps/panel
COPY apps/panel/package.json apps/panel/package-lock.json ./
RUN npm ci
COPY apps/panel ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/apps/agent
COPY apps/agent/pyproject.toml apps/agent/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/agent ./
RUN uv sync --frozen --no-dev
COPY --from=panel /app/apps/panel/dist /app/apps/panel/dist
# data/ is the volume: the store and its images outlive deploys.
CMD ["uv", "run", "--no-sync", "python", "-m", "agent"]
