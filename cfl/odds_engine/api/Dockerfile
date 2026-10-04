FROM node:20-alpine

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --production
COPY server.js ./

EXPOSE 7000
CMD ["npm", "start"]
