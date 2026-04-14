FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

# Configure pip mirror: Aliyun PyPI mirror
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config unset global.extra-index-url || true && \
    pip config set global.trusted-host mirrors.aliyun.com

# Install only the dependencies required by web service
RUN pip install --retries 10 --timeout 120 flask
RUN pip install --retries 10 --timeout 120 gunicorn

COPY . /app

ARG BUILD_VERSION=dev
RUN find /app -name '*.html' -exec sed -i "s/\?v=[0-9a-zA-Z_]*/?v=${BUILD_VERSION}/g" {} +
