FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
COPY tsconfig.json .
RUN npm ci

COPY src ./src

CMD ["npm", "run", "start"]
