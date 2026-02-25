FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY config.py .
COPY src/ src/

# Data volume for SQLite
VOLUME /data

ENV MMUD_DB_PATH=/data/mmud.db
ENV MMUD_WEB_HOST=0.0.0.0
ENV MMUD_WEB_PORT=5000

EXPOSE 5000

CMD ["python", "-m", "src.main", "--db", "/data/mmud.db"]
