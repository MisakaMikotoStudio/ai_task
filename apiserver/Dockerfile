FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install runtime dependencies (one layer per package for better cache reuse)
RUN pip install "flask>=2.0.0"
RUN pip install "flask-cors>=4.0.0"
RUN pip install "werkzeug>=2.0.0"
RUN pip install "pymysql>=1.0.0"
RUN pip install "sqlalchemy>=2.0.0"
RUN pip install "tomli>=2.0.0"
