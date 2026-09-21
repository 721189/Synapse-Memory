# Production Dockerfile for SynapseMemory FastAPI Proxy Service
FROM python:3.11-slim

WORKDIR /app

# Set environment variables for Python compiler efficiency
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Install lightweight system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements manifest and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files cleanly
COPY . .

# Expose port 8000 for standard ingress routing
EXPOSE 8000

# Run FastAPI production server with uvicorn
CMD ["python", "synapse_memory/api/fastapi_server.py"]
