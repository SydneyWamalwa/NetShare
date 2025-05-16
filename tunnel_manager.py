#!/usr/bin/env python3
import os
import time
import logging
import sqlite3
import requests
import uuid
import string
import random
import subprocess
import tempfile
from string import Template

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database connection
DB_URL = os.getenv('DATABASE_URL', 'netshare.db')

def get_db_connection():
    conn = sqlite3.connect(DB_URL)
    conn.row_factory = sqlite3.Row
    return conn

def generate_credentials():
    """Generate random username and password for FRP SOCKS5 proxy"""
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    return username, password

def assign_port():
    """Assign an available port for the FRP tunnel"""
    # Start from 9000 and find the next available port
    conn = get_db_connection()
    used_ports = [row['port'] for row in conn.execute('SELECT port FROM connections WHERE status = "active"')]
    conn.close()

    port = 9000
    while port in used_ports and port < 10000:
        port += 1

    if port >= 10000:
        raise Exception("No ports available in the allowed range")

    return port

def create_tunnel(sharer_phone):
    """Create a new FRP tunnel for a sharer"""
    try:
        # Generate credentials for the proxy
        username, password = generate_credentials()
        port = assign_port()

        # Create a connection record
        conn = get_db_connection()
        connection_id = str(uuid.uuid4())

        # Insert new connection
        conn.execute(
            'INSERT INTO connections (id, sharer_phone, fly_instance, status, port, username, password) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (connection_id, sharer_phone, f"netshare-{sharer_phone[-4:]}", "active", port, username, password)
        )
        conn.commit()
        conn.close()

        logger.info(f"Created tunnel for {sharer_phone} on port {port}")
        return {
            'success': True,
            'connection_id': connection_id,
            'port': port,
            'username': username,
            'password': password
        }
    except Exception as e:
        logger.error(f"Failed to create tunnel: {str(e)}")
        return {'success': False, 'error': str(e)}

def terminate_tunnel(connection_id):
    """Terminate an active tunnel"""
    try:
        conn = get_db_connection()
        conn.execute('UPDATE connections SET status = "terminated" WHERE id = ?', (connection_id,))
        conn.commit()

        # Get the details to update the client if necessary
        connection = conn.execute('SELECT client_phone FROM connections WHERE id = ?', (connection_id,)).fetchone()
        if connection and connection['client_phone']:
            conn.execute('UPDATE users SET connection_id = NULL WHERE phone = ?', (connection['client_phone'],))
            conn.commit()

        conn.close()
        logger.info(f"Terminated tunnel {connection_id}")
        return {'success': True}
    except Exception as e:
        logger.error(f"Failed to terminate tunnel: {str(e)}")
        return {'success': False, 'error': str(e)}

def monitor_connections():
    """Monitor active connections and manage FRP processes"""
    while True:
        try:
            conn = get_db_connection()

            # Get active connections
            active_connections = conn.execute(
                'SELECT c.id, c.sharer_phone, c.port, c.username, c.password, '
                'u.sharing_active FROM connections c JOIN users u ON c.sharer_phone = u.phone '
                'WHERE c.status = "active"'
            ).fetchall()

            for connection in active_connections:
                # If sharer has disabled sharing, terminate the connection
                if not connection['sharing_active']:
                    terminate_tunnel(connection['id'])
                    logger.info(f"Terminated tunnel {connection['id']} due to sharing disabled")

            conn.close()

        except Exception as e:
            logger.error(f"Error in connection monitor: {str(e)}")

        # Sleep for 60 seconds before next check
        time.sleep(60)

if __name__ == "__main__":
    logger.info("Starting tunnel manager...")

    # Create necessary database tables if they don't exist
    conn = get_db_connection()
    conn.execute('''
    CREATE TABLE IF NOT EXISTS connections (
        id TEXT PRIMARY KEY,
        sharer_phone TEXT,
        client_phone TEXT,
        fly_instance TEXT,
        status TEXT,
        port INTEGER,
        username TEXT,
        password TEXT,
        bandwidth_used REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

    # Start monitoring connections
    monitor_connections()