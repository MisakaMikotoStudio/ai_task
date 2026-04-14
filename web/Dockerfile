FROM nginx:alpine

WORKDIR /app

# Copy all files into container
COPY . /app

# Setup: move nginx config and entrypoint out of served directory,
# then clean non-static files (Python source, build files) to prevent leakage
RUN mv /app/nginx.conf /etc/nginx/conf.d/default.conf && \
    mv /app/entrypoint.sh /entrypoint.sh && \
    chmod +x /entrypoint.sh && \
    rm -f /app/*.py /app/*.pyc /app/Dockerfile /app/*.toml && \
    rm -rf /app/__pycache__

# Inject BUILD_VERSION into HTML ?v= params for cache busting
ARG BUILD_VERSION=dev
RUN find /app -name '*.html' -exec sed -i "s/?v=[0-9a-zA-Z_]*/?v=${BUILD_VERSION}/g" {} +

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
