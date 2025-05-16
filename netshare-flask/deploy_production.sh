#!/bin/bash
# NetShare Production Deployment Script
# This script configures NetShare Flask application for production use

# Exit on error
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print colored messages
print_message() {
  echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then
  print_error "Please run as root or with sudo"
  exit 1
fi

# Check if we're in the netshare-flask directory
if [ ! -f "app.py" ] || [ ! -d "templates" ]; then
  print_error "Please run this script from the netshare-flask directory"
  exit 1
fi

# Get current directory
APP_DIR=$(pwd)
APP_USER=$(logname)
DOMAIN="netshare.example.com" # Change this to your actual domain

print_message "Starting NetShare production setup in: $APP_DIR"

# 1. Install system dependencies
print_message "Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv nginx certbot python3-certbot-nginx supervisor

# 2. Set up Python virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
  print_message "Setting up Python virtual environment..."
  python3 -m venv venv
fi

# 3. Install Python dependencies
print_message "Installing Python dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn # Add gunicorn for production

# 4. Create production .env file if it doesn't exist
if [ ! -f ".env" ]; then
  print_message "Creating production .env file..."
  RANDOM_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
  cat > .env << EOF
FLASK_APP=app.py
FLASK_ENV=production
SECRET_KEY=$RANDOM_SECRET
PORT=8000
EOF
  print_message "Created .env file with random secret key"
else
  print_warning ".env file already exists, skipping"
fi

# 5. Configure Supervisor for process management
print_message "Configuring Supervisor..."
cat > /etc/supervisor/conf.d/netshare.conf << EOF
[program:netshare]
directory=$APP_DIR
command=$APP_DIR/venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 app:app
user=$APP_USER
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/netshare/gunicorn.log
stderr_logfile=/var/log/netshare/gunicorn.error.log
EOF

# Create log directory
mkdir -p /var/log/netshare
chown -R $APP_USER:$APP_USER /var/log/netshare

# 6. Configure Nginx as reverse proxy
print_message "Configuring Nginx..."
cat > /etc/nginx/sites-available/netshare << EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /static {
        alias $APP_DIR/static;
        expires 30d;
    }

    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
}
EOF

# Enable the site
ln -sf /etc/nginx/sites-available/netshare /etc/nginx/sites-enabled/

# 7. Update application for production
print_message "Updating application for production..."

# Create production update script
cat > update_production.py << EOF
#!/usr/bin/env python3
import re

# Update the app.py file for production
with open('app.py', 'r') as file:
    content = file.read()

# Replace debug=True with debug=False if needed
content = re.sub(r'app\.run\(debug=True', 'app.run(debug=False', content)

# Make sure we use the right port from env
if 'app.run(debug=False)' in content:
    content = content.replace(
        'app.run(debug=False)', 
        'app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))'
    )

with open('app.py', 'w') as file:
    file.write(content)

print("Updated app.py for production")

# Update CSS to include some production optimizations
with open('static/css/custom.css', 'a') as file:
    file.write("""
/* Production performance optimizations */
img {
    max-width: 100%;
    height: auto;
}

/* Improve mobile usability */
@media (max-width: 768px) {
    .container {
        padding-left: 15px;
        padding-right: 15px;
    }
}
""")

print("Updated CSS for production")
EOF

chmod +x update_production.py
python3 update_production.py

# 8. Set correct permissions
print_message "Setting correct permissions..."
chown -R $APP_USER:$APP_USER $APP_DIR
chmod -R 755 $APP_DIR/static

# 9. Start/reload services
print_message "Starting services..."
systemctl daemon-reload
systemctl restart supervisor
systemctl restart nginx

# 10. Setup SSL with Certbot
print_message "Do you want to set up SSL with Let's Encrypt? (y/n)"
read -r setup_ssl

if [ "$setup_ssl" = "y" ]; then
    print_message "Setting up SSL with Let's Encrypt..."
    certbot --nginx -d $DOMAIN
    print_message "SSL setup complete!"
else
    print_warning "Skipping SSL setup. Remember that production sites should use HTTPS."
fi

# 11. Create basic monitoring script
print_message "Creating monitoring script..."
cat > monitor.sh << 'EOF'
#!/bin/bash
# Simple monitoring script for NetShare Flask application

# Check if Gunicorn is running
if ! pgrep -f gunicorn > /dev/null; then
    echo "ERROR: Gunicorn is not running!"
    echo "Attempting to restart..."
    sudo systemctl restart supervisor
    sleep 5
    if ! pgrep -f gunicorn > /dev/null; then
        echo "CRITICAL: Failed to restart Gunicorn!"
        # Send notification (email, SMS, etc.)
    else
        echo "Gunicorn restarted successfully."
    fi
else
    echo "Gunicorn is running."
fi

# Check if Nginx is running
if ! systemctl is-active --quiet nginx; then
    echo "ERROR: Nginx is not running!"
    echo "Attempting to restart..."
    sudo systemctl restart nginx
    sleep 2
    if ! systemctl is-active --quiet nginx; then
        echo "CRITICAL: Failed to restart Nginx!"
        # Send notification
    else
        echo "Nginx restarted successfully."
    fi
else
    echo "Nginx is running."
fi

# Check if site is accessible
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/)
if [ "$HTTP_STATUS" != "200" ]; then
    echo "ERROR: Website is not accessible! Status code: $HTTP_STATUS"
    # Send notification
else
    echo "Website is accessible."
fi

# Check disk space
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 90 ]; then
    echo "WARNING: Disk usage is high ($DISK_USAGE%)!"
    # Send notification
fi

# Log check results
echo "$(date): Monitoring completed." >> /var/log/netshare/monitoring.log
EOF

chmod +x monitor.sh

# 12. Setup cron job for monitoring
print_message "Setting up monitoring cron job..."
(crontab -l 2>/dev/null; echo "*/15 * * * * $APP_DIR/monitor.sh >> /var/log/netshare/cron.log 2>&1") | crontab -

# 13. Create backup script
print_message "Creating backup script..."
cat > backup.sh << 'EOF'
#!/bin/bash
# Backup script for NetShare Flask application

BACKUP_DIR="/var/backups/netshare"
APP_DIR=$(dirname "$0")
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/netshare_backup_$TIMESTAMP.tar.gz"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create backup
tar -czf "$BACKUP_FILE" -C "$APP_DIR" \
    --exclude=".git" \
    --exclude="venv" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    .

echo "Backup created: $BACKUP_FILE"

# Keep only the last 10 backups
ls -t "$BACKUP_DIR"/netshare_backup_*.tar.gz | tail -n +11 | xargs -r rm

# Log backup completion
echo "$(date): Backup completed - $BACKUP_FILE" >> "$BACKUP_DIR/backup.log"
EOF

chmod +x backup.sh

# 14. Setup daily backup cron job
print_message "Setting up backup cron job..."
(crontab -l 2>/dev/null; echo "0 2 * * * $APP_DIR/backup.sh >> /var/log/netshare/backup.log 2>&1") | crontab -

# Create backup directory
mkdir -p /var/backups/netshare
chown -R $APP_USER:$APP_USER /var/backups/netshare

# 15. Final touches and helpful guidance
print_message "Creating production README..."
cat > PRODUCTION_README.md << EOF
# NetShare Production Deployment

This NetShare application has been configured for production use.

## System Architecture

- Flask application running with Gunicorn WSGI server
- Nginx as a reverse proxy
- Supervisor for process management
- Let's Encrypt SSL (if enabled)
- Automated monitoring and backup

## Management Commands

### Service Management
- Restart application: \`sudo systemctl restart supervisor\`
- Restart web server: \`sudo systemctl restart nginx\`
- View application logs: \`tail -f /var/log/netshare/gunicorn.log\`
- View error logs: \`tail -f /var/log/netshare/gunicorn.error.log\`

### Maintenance
- Run backup manually: \`./backup.sh\`
- Run monitoring manually: \`./monitor.sh\`

### Updating the Application
1. Pull the latest code
2. Activate virtual environment: \`source venv/bin/activate\`
3. Install any new dependencies: \`pip install -r requirements.txt\`
4. Restart the service: \`sudo systemctl restart supervisor\`

## Security Considerations
- Keep your system and packages updated
- Regularly check logs for unusual activity
- Consider setting up a firewall (UFW)
- Implement rate limiting if needed
EOF

# 16. Create UFW firewall setup script (optional)
print_message "Creating firewall setup script..."
cat > setup_firewall.sh << 'EOF'
#!/bin/bash
# UFW Firewall setup for NetShare Flask application

# Install UFW if not already installed
apt-get update
apt-get install -y ufw

# Reset UFW to default
ufw --force reset

# Set default policies
ufw default deny incoming
ufw default allow outgoing

# Allow SSH (modify port if needed)
ufw allow 22/tcp

# Allow HTTP and HTTPS
ufw allow 80/tcp
ufw allow 443/tcp

# Enable UFW
echo "y" | ufw enable

# Show status
ufw status verbose
EOF

chmod +x setup_firewall.sh

print_message "==================================================="
print_message "NetShare Production Setup Complete!"
print_message "==================================================="
print_message "Your application is now running at: http://$DOMAIN"
if [ "$setup_ssl" = "y" ]; then
    print_message "HTTPS is enabled at: https://$DOMAIN"
fi
print_message ""
print_message "To set up a firewall (recommended):"
print_message "  sudo ./setup_firewall.sh"
print_message ""
print_message "For more information, see PRODUCTION_README.md"
print_message "==================================================="
