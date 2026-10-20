# syntax=docker/dockerfile:1
# One Dockerfile, three targets, used by compose.yaml (`docker compose up`):
#   contracts  Foundry: compiles the contracts once; runs the local chain (anvil) and the deploy + seed step
#   api        the agent, guard and FastAPI server, with the compiled ABIs
#   web        the Next.js dashboard

# ---------------------------------------------------------------- contracts
FROM ghcr.io/foundry-rs/foundry:v1.8.5 AS contracts
USER root
WORKDIR /hufu/contracts
COPY contracts/foundry.toml ./
COPY contracts/src ./src
COPY contracts/script ./script
RUN git init -q \
 && forge install foundry-rs/forge-std@v1.17.0 --no-git \
 && forge install OpenZeppelin/openzeppelin-contracts@v5.6.1 --no-git \
 && forge build \
 && mkdir -p deployments

# ---------------------------------------------------------------- api
FROM python:3.12-slim-bookworm AS api
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /hufu/agent
COPY agent/pyproject.toml agent/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agent/hufu ./hufu
COPY agent/data ./data
RUN uv sync --frozen --no-dev
COPY --from=contracts /hufu/contracts/out /hufu/contracts/out
ENV PATH=/hufu/agent/.venv/bin:$PATH
EXPOSE 8000
CMD ["uvicorn", "hufu.api:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- web
FROM node:22-bookworm-slim AS web
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm install -g pnpm@12.3.4
WORKDIR /hufu/web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# The browser calls the API directly, so its address is baked into the build.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN pnpm build
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "--port", "3000", "--hostname", "0.0.0.0"]
