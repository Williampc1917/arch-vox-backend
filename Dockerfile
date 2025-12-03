# Multi-stage build for Cloud Run
FROM python:3.11-slim as builder

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY app/ ./app/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Production stage
FROM python:3.11-slim as production

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy built application from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app ./
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Change ownership to non-root user
RUN chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/entrypoint.sh"]
