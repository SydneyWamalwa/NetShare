# --------------------
# Stage 1: Builder
# --------------------
    FROM python:3.9-slim as builder

    WORKDIR /app

    # Install build dependencies
    RUN apt-get update && \
        apt-get install -y --no-install-recommends gcc python3-dev libffi-dev && \
        rm -rf /var/lib/apt/lists/*

    # Create virtual environment
    RUN python -m venv /opt/venv

    # Ensure pip is up to date
    ENV PATH="/opt/venv/bin:$PATH"
    COPY requirements.txt .
    RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

    # --------------------
    # Stage 2: Runtime
    # --------------------
    FROM python:3.9-slim

    WORKDIR /app

    # Copy virtualenv from builder
    COPY --from=builder /opt/venv /opt/venv

    # Install runtime dependencies
    RUN apt-get update && \
        apt-get install -y --no-install-recommends \
        curl \
        tar \
        speedtest-cli \
        iputils-ping && \
        rm -rf /var/lib/apt/lists/*

    # Install FRP
    RUN curl -L -o frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.58.0/frp_0.58.0_linux_amd64.tar.gz && \
        tar -xzf frp.tar.gz && \
        cp frp_0.58.0_linux_amd64/frps /usr/local/bin/frps && \
        chmod +x /usr/local/bin/frps && \
        rm -rf frp.tar.gz frp_0.58.0_linux_amd64

    # Copy application code
    COPY . .

    # Copy and allow execution of entrypoint script
    COPY entrypoint.sh /app/
    RUN chmod +x /app/entrypoint.sh

    # Set PATH to include venv
    ENV PATH="/opt/venv/bin:$PATH"

    # Expose Flask and FRP dashboard ports
    EXPOSE 5000 8080 7000 7500

    # Create non-root user
    RUN useradd -m appuser && chown -R appuser /app

    # Switch to non-root user
    USER appuser

    # Health check for Flask
    HEALTHCHECK --interval=30s --timeout=3s \
        CMD curl -f http://localhost:5000/health || exit 1

    # Entrypoint
    ENTRYPOINT ["/app/entrypoint.sh"]
