# Dockerfile
FROM python:3.9-slim

WORKDIR /app

# Copy and install all dependencies from one file
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy your app code
COPY . .

# Expose ports (no inline comments!)
EXPOSE 8080
EXPOSE 5000

# Install curl, tar, and other dependencies
RUN apt-get update && apt-get install -y curl tar && rm -rf /var/lib/apt/lists/*

# Download and install FRP
RUN curl -L -o frp.tar.gz https://github.com/fatedier/frp/releases/download/v0.58.0/frp_0.58.0_linux_amd64.tar.gz \
 && tar -xzf frp.tar.gz \
 && cp frp_0.58.0_linux_amd64/frps /usr/local/bin/frps \
 && chmod +x /usr/local/bin/frps \
 && rm -rf frp.tar.gz frp_0.58.0_linux_amd64


# Entrypoint to launch both
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
