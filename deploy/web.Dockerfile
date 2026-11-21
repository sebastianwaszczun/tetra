FROM node:22.11.0-slim AS front
WORKDIR /front
COPY front/package.json ./
RUN npm install --no-audit --no-fund
COPY front/index.html front/vite.config.js ./
COPY front/src ./src
ENV VITE_API_URL=/api
RUN npx vite build

FROM caddy:2.8.4
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=front /front/dist /srv
