# Campus AI frontend - Next.js 16 (React 19)
# Multi-stage build: install deps -> build -> slim production runner.
#
# IMPORTANT: NEXT_PUBLIC_* values AND the security-header inputs
# (CSP_CONNECT_SRC_EXTRA, HSTS_MAX_AGE, HSTS_INCLUDE_SUBDOMAINS - see
# next.config.ts) are baked in at BUILD time, so build one image per
# environment with the right --build-arg values.
#
# Phase 7 hardening: the runner uses Next's "standalone" output (only the
# traced runtime files, no devDependencies, no full node_modules) and runs as
# the unprivileged `node` user with root-owned, read-only app files.

FROM node:24.22.3-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24.22.3-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Local default kept so `docker compose up --build` (dev) still works; a
# production build MUST pass the real API origin.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_API_PREFIX=/v1
ARG CSP_CONNECT_SRC_EXTRA=""
ARG HSTS_MAX_AGE=0
# Phase 5 master interview build flag (default off). Inlined at build time; it
# also decides the global microphone Permissions-Policy (next.config.ts).
ARG NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=false
# Phase 6 video build flag (default off). Read at build time: it is inlined
# into the bundle and decides the mock-interview camera Permissions-Policy.
ARG NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED=false
ARG NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED=false
# Phase 8.2 coding build flag (default off); inlined at build time.
ARG NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED=false
ARG HSTS_INCLUDE_SUBDOMAINS=false
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_API_PREFIX=$NEXT_PUBLIC_API_PREFIX \
    CSP_CONNECT_SRC_EXTRA=$CSP_CONNECT_SRC_EXTRA \
    HSTS_MAX_AGE=$HSTS_MAX_AGE \
    HSTS_INCLUDE_SUBDOMAINS=$HSTS_INCLUDE_SUBDOMAINS \
    NEXT_OUTPUT_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24.22.3-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# Root-owned app files; only Next's runtime cache dir is writable by `node`.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
RUN mkdir -p .next/cache && chown node:node .next/cache
USER node
EXPOSE 3000
CMD ["node", "server.js"]
