FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

# Configure pip mirror: Aliyun PyPI mirror
# (Avoid extra-index fallback to official PyPI to reduce stalls in restricted networks.)
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config unset global.extra-index-url || true && \
    pip config set global.trusted-host mirrors.aliyun.com

# Install runtime dependencies (one layer per package for better cache reuse)
RUN pip install  --retries 10 "flask>=2.0.0"
RUN pip install  --retries 10 "flask-cors>=4.0.0"
RUN pip install  --retries 10 "werkzeug>=2.0.0"
RUN pip install  --retries 10 "pymysql>=1.0.0"
RUN pip install  --retries 10 "sqlalchemy>=2.0.0"
RUN pip install  --retries 10 "tomli>=2.0.0"
RUN pip install  --retries 10 "cryptography>=42.0.0"
RUN pip install  --retries 10 "cos-python-sdk-v5"
RUN pip install  --retries 10 "qcloud-python-sts"
RUN pip install  --retries 10 "paramiko>=3.0.0"
RUN pip install  --retries 10 "alibabacloud_rds20140815"
RUN pip install  --retries 10 "alibabacloud_tea_openapi"
RUN pip install  --retries 10 "PyJWT[crypto]>=2.0.0"
RUN pip install  --retries 10 "requests>=2.28.0"
RUN pip install  --retries 10 "gunicorn>=21.2.0"
RUN pip install  --retries 10 "gevent>=24.2.0"

COPY . /app
