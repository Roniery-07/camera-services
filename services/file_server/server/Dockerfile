FROM node:18

# Diretório de trabalho
WORKDIR /app

# Copia os arquivos da aplicação
COPY package*.json ./
RUN npm install

COPY file_server.js ./
COPY .env ./
RUN mkdir -p /app/videos

# Porta usada no servidor
EXPOSE 8000

CMD ["node", "file_server.js"]
