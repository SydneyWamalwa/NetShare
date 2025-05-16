#!/usr/bin/env bash
set -e

# launch FRP server (reads frps.ini)
frps -c /app/frps.ini &

# wait a moment for frps to start
sleep 2

# launch Flask
exec gunicorn --bind 0.0.0.0:5000 wsgi:app

