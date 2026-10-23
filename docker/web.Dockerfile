FROM node:22.23.4-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM nginx:1.30.6-alpine
# Refuse an image containing the pre-fix Brotli decoder, even if its base
# image was built before the upstream security release.
RUN apk add --no-cache --upgrade 'brotli-libs>=1.2.1-r0'
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

