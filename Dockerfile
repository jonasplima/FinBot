ARG PYTHON_BUILDER_IMAGE=python:3.12-slim
ARG PYTHON_RUNTIME_IMAGE=python:3.12-slim

FROM ${PYTHON_BUILDER_IMAGE} AS builder

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install -r requirements.txt

FROM ${PYTHON_RUNTIME_IMAGE}

WORKDIR /app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=builder /opt/venv /opt/venv

# Create non-root user for security
RUN useradd -m -u 1000 finbot && chown -R finbot:finbot /app
COPY --chown=finbot:finbot . .
USER finbot

# Expose port
EXPOSE 3003

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3003"]
