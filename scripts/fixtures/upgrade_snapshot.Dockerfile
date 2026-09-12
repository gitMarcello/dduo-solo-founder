ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE}
USER root
RUN apk add --no-cache docker-cli
USER 1000:1000
