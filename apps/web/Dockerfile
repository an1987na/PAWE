FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
RUN pnpm install --no-frozen-lockfile
COPY apps/web ./apps/web
COPY packages/contracts ./packages/contracts
RUN pnpm --filter @pawe/web build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/apps/web/dist ./dist
CMD ["serve", "-s", "dist", "-l", "4173"]
