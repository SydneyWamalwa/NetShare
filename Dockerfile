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

# Entrypoint to launch both
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
