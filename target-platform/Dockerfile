FROM node:22-alpine
ARG SERVICE
WORKDIR /app
COPY shared ./shared
COPY ${SERVICE}/package.json ./${SERVICE}/
RUN cd ${SERVICE} && npm install --omit=dev --no-audit --no-fund
COPY ${SERVICE} ./${SERVICE}
WORKDIR /app/${SERVICE}
ENV NODE_ENV=production
CMD ["node", "server.js"]
