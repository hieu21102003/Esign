FROM node:24-slim AS build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
COPY signer/fonts/NotoSans-Regular.ttf ./public/fonts/NotoSans-Regular.ttf
RUN npm test && npm run build

FROM nginx:1.27-alpine
RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /var/cache/nginx /var/run/nginx /tmp \
    && chown -R nginx:nginx /var/cache/nginx /var/run/nginx /usr/share/nginx/html /etc/nginx/conf.d \
    && sed -i 's|/run/nginx.pid|/tmp/nginx.pid|' /etc/nginx/nginx.conf
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
