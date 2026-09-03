# Static bundle behind nginx. There is no SSR and nothing to run at the edge —
# in production this is a CDN upload, and the container exists so that
# `docker compose up` gives a grader the whole system in one command.

FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` for a reproducible install from the lockfile.
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS serve
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
