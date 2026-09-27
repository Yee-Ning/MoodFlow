# Reuse the vendored app.js/tini runtime. The upstream Dockerfile copies .env
# and uses unpinned Yarn despite its pnpm lock; keep the submodule untouched.
FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34
RUN apk add --no-cache tini && corepack enable && corepack prepare pnpm@10.11.0 --activate
WORKDIR /app
COPY netease-music-api/package.json netease-music-api/pnpm-lock.yaml ./
RUN pnpm install --prod --frozen-lockfile --ignore-scripts
COPY --chown=node:node netease-music-api/ ./
USER node
ENV NODE_ENV=production PORT=3000 HOST=0.0.0.0
EXPOSE 3000
ENTRYPOINT ["/sbin/tini", "--", "node", "app.js"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 CMD node -e "fetch('http://127.0.0.1:3000/inner/version').then(async r=>{if(!r.ok||(await r.json()).code!==200)process.exit(1)}).catch(()=>process.exit(1))"
