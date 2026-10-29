ARG NODE_IMAGE=node:22.23.4-alpine
FROM ${NODE_IMAGE}
USER root
RUN apk add --no-cache docker-cli
USER 1000:1000
