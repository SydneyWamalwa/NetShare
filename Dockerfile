# --------------------
# Stage 1: Builder
# --------------------
    FROM python:3.9-slim as builder

    WORKDIR /app

    # Install build dependencies
    RUN apt-get update && \
        apt-get install -y --no-install-recommends gcc python3-dev && \
        rm -rf /var/lib/apt/lists/*

    # Install Python dependencies
    COPY requirements.txt .
    RUN pip install --user --no-cache-dir -r requirements.txt

    # --------------------
    # Stage 2: Runtime
    # --------------------
    FROM python:3.9-slim

    WORKDIR /app

    # Copy installed Python packages from builder
    COPY --from=builder /root/.local /root/.local

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

    # Copy entrypoint script and set permissions BEFORE switching user
    COPY entrypoint.sh /app/
    RUN chmod +x /app/entrypoint.sh

    # Ensure PATH includes user-installed binaries
    ENV PATH=/root/.local/bin:$PATH

    # Expose ports (Flask and FRP)
    EXPOSE 5000 8080

    # Create a non-root user and change ownership
    RUN useradd -m appuser && chown -R appuser /app

    # Switch to non-root user
    USER appuser

    ENV PATH=/root/.local/bin:$PATH

    # Health check
    HEALTHCHECK --interval=30s --timeout=3s \
        CMD curl -f http://localhost:5000/health || exit 1

    # Entrypoint
    ENTRYPOINT ["/app/entrypoint.sh"]
