FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 finbot && chown -R finbot:finbot /app
USER finbot

# Expose port
EXPOSE 3003

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3003"]
