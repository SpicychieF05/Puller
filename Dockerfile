# Dockerfile for Puller — python:3.12-slim base
FROM python:3.12-slim

# Install ffmpeg (no recommended packages, clean lists afterwards)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno >= 2.3.0 using the official installer
RUN curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/deno \
    && deno --version

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source
COPY main.py .
COPY static/ ./static/
COPY media/ ./media/

# Run as a non-root user
RUN useradd --create-home appuser
USER appuser

# Shell form so $PORT expands at runtime
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips "*"
