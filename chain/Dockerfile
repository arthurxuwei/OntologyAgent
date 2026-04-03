# syntax=docker/dockerfile:1.7
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
COPY tsconfig.json .
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY src ./src

CMD ["npm", "run", "start"]
