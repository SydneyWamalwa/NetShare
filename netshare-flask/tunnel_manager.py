import logging
import time
import os
import sys
import json
import requests
import threading
import sqlite3
import uuid
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("tunnel_manager.log")
    ]
)
logger = logging.getLogger("tunnel_manager")

# Load configuration
from config import Config

# Database connection
def get_db_connection():
    conn = sqlite3.connect(os.path.basename(Config.SQLALCHEMY_DATABASE_URI.replace('sqlite:///', '')))
    conn.row_factory = sqlite3.Row
    return conn

class TunnelManager:
    def __init__(self):
        self.active_tunnels = {}  # Map of tunnel_id -> tunnel_info
        self.fly_api_url = Config.FLY_API_URL
        self.fly_api_key = Config.FLY_API_KEY
        self.fly_app_name = Config.FLY_APP_NAME
        self.fly_org = Config.FLY_ORG
        
    def get_headers(self):
        return {
            'Authorization': f'Bearer {self.fly_api_key}',
            'Content-Type': 'application/json'
        }
    
    def create_tunnel(self, sharer_phone):
        """Create a new fly.io tunnel instance for a sharer"""
        try:
            # Generate a unique instance name
            instance_name = f"netshare-{sharer_phone[-4:]}-{int(time.time())}"
            
            # Create the fly app instance
            logger.info(f"Creating fly.io tunnel for {sharer_phone}: {instance_name}")
            
            # In production, this would use the fly.io API to create a new instance
            # For now, we'll simulate a successful creation
            
            tunnel_id = str(uuid.uuid4())
            tunnel_info = {
                'id': tunnel_id,
                'instance_name': instance_name,
                'sharer_phone': sharer_phone,
                'status': 'active',
                'created_at': datetime.utcnow().isoformat(),
                'last_checked': datetime.utcnow().isoformat(),
                'clients': [],
                'bandwidth_usage': 0.0,
                'health': 'good'
            }
            
            # Store in active tunnels map
            self.active_tunnels[tunnel_id] = tunnel_info
            
            # Create a database record
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO connection (id, sharer_phone, fly_instance, status, created_at, last_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                tunnel_id,
                sharer_phone,
                instance_name,
                'active',
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat()
            ))
            conn.commit()
            conn.close()
            
            logger.info(f"Tunnel created successfully: {tunnel_id}")
            return tunnel_info
        
        except Exception as e:
            logger.error(f"Error creating tunnel: {str(e)}")
            return None
    
    def terminate_tunnel(self, tunnel_id):
        """Terminate a fly.io tunnel instance"""
        try:
            if tunnel_id not in self.active_tunnels:
                logger.warning(f"Tunnel {tunnel_id} not found in active tunnels.")
                return False
            
            tunnel_info = self.active_tunnels[tunnel_id]
            logger.info(f"Terminating fly.io tunnel: {tunnel_id} ({tunnel_info['instance_name']})")
            
            # In production, this would use the fly.io API to destroy the instance
            # For now, we'll simulate a successful termination
            
            # Remove from active tunnels map
            self.active_tunnels.pop(tunnel_id, None)
            
            # Update database record
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE connection 
                SET status = 'terminated', last_active = ?
                WHERE id = ?
            ''', (
                datetime.utcnow().isoformat(),
                tunnel_id
            ))
            conn.commit()
            conn.close()
            
            logger.info(f"Tunnel terminated successfully: {tunnel_id}")
            return True
        
        except Exception as e:
            logger.error(f"Error terminating tunnel: {str(e)}")
            return False
    
    def connect_client(self, tunnel_id, client_phone):
        """Connect a client to an existing tunnel"""
        try:
            if tunnel_id not in self.active_tunnels:
                logger.warning(f"Tunnel {tunnel_id} not found in active tunnels.")
                return False
            
            tunnel_info = self.active_tunnels[tunnel_id]
            
            # Add client to the tunnel
            if client_phone not in tunnel_info['clients']:
                tunnel_info['clients'].append(client_phone)
            
            # Update database record
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE connection 
                SET client_phone = ?, last_active = ?
                WHERE id = ?
            ''', (
                client_phone,
                datetime.utcnow().isoformat(),
                tunnel_id
            ))
            
            # Also update the user's connection_id
            cursor.execute('''
                UPDATE user
                SET connection_id = ?
                WHERE phone = ?
            ''', (
                tunnel_id,
                client_phone
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Client {client_phone} connected to tunnel {tunnel_id}")
            return True
        
        except Exception as e:
            logger.error(f"Error connecting client to tunnel: {str(e)}")
            return False
    
    def disconnect_client(self, client_phone):
        """Disconnect a client from any tunnel they're connected to"""
        try:
            # Find tunnels that the client is connected to
            for tunnel_id, tunnel_info in self.active_tunnels.items():
                if client_phone in tunnel_info['clients']:
                    # Remove client from the tunnel
                    tunnel_info['clients'].remove(client_phone)
                    
                    # Update database connection record
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    
                    # If this was the only client, set client_phone to NULL
                    if not tunnel_info['clients']:
                        cursor.execute('''
                            UPDATE connection 
                            SET client_phone = NULL, last_active = ?
                            WHERE id = ?
                        ''', (
                            datetime.utcnow().isoformat(),
                            tunnel_id
                        ))
                    
                    # Update the user record to remove connection_id
                    cursor.execute('''
                        UPDATE user
                        SET connection_id = NULL
                        WHERE phone = ?
                    ''', (
                        client_phone,
                    ))
                    
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Client {client_phone} disconnected from tunnel {tunnel_id}")
                    return True
            
            logger.warning(f"Client {client_phone} not found in any active tunnels")
            return False
        
        except Exception as e:
            logger.error(f"Error disconnecting client: {str(e)}")
            return False
    
    def monitor_tunnels(self):
        """Monitor active tunnels for health and stability"""
        logger.info("Starting tunnel monitoring...")
        
        while True:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Get all active connections from the database
                cursor.execute('''
                    SELECT id, sharer_phone, client_phone, fly_instance, status, created_at, last_active
                    FROM connection
                    WHERE status = 'active'
                ''')
                active_connections = cursor.fetchall()
                
                for conn_record in active_connections:
                    tunnel_id = conn_record['id']
                    
                    # Check if the tunnel is in our active tunnels map
                    if tunnel_id not in self.active_tunnels:
                        # Add to our active tunnels map
                        self.active_tunnels[tunnel_id] = {
                            'id': tunnel_id,
                            'instance_name': conn_record['fly_instance'],
                            'sharer_phone': conn_record['sharer_phone'],
                            'status': conn_record['status'],
                            'created_at': conn_record['created_at'],
                            'last_checked': datetime.utcnow().isoformat(),
                            'clients': [conn_record['client_phone']] if conn_record['client_phone'] else [],
                            'bandwidth_usage': 0.0,
                            'health': 'good'
                        }
                    
                    # Check if the tunnel is healthy
                    self._check_tunnel_health(tunnel_id)
                    
                    # Update tunnel last_checked timestamp
                    self.active_tunnels[tunnel_id]['last_checked'] = datetime.utcnow().isoformat()
                
                conn.close()
                
                # Sleep for the configured interval
                time.sleep(Config.STABILITY_CHECK_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in tunnel monitoring: {str(e)}")
                time.sleep(10)  # Sleep briefly before retrying
    
    def _check_tunnel_health(self, tunnel_id):
        """Check the health of a specific tunnel"""
        try:
            if tunnel_id not in self.active_tunnels:
                return False
            
            tunnel_info = self.active_tunnels[tunnel_id]
            instance_name = tunnel_info['instance_name']
            
            # In production, this would involve:
            # 1. Checking the fly.io instance status via API
            # 2. Running ping tests to check latency
            # 3. Running bandwidth tests to check throughput
            # 4. Checking error logs for any issues
            
            # For now, we'll simulate health checks with random statuses
            import random
            health_status = random.choices(
                ['good', 'fair', 'poor'], 
                weights=[0.8, 0.15, 0.05], 
                k=1
            )[0]
            
            # Update health status and also update bandwidth usage
            tunnel_info['health'] = health_status
            
            # Simulate bandwidth usage
            if tunnel_info['clients']:
                # If there are clients, increase bandwidth usage
                tunnel_info['bandwidth_usage'] += 0.01 * len(tunnel_info['clients']) * random.uniform(0.5, 1.5)
                
                # Update the database record
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE connection 
                    SET bandwidth_used = ?, last_active = ?
                    WHERE id = ?
                ''', (
                    tunnel_info['bandwidth_usage'],
                    datetime.utcnow().isoformat(),
                    tunnel_id
                ))
                
                # Also update the sharer's usage
                cursor.execute('''
                    UPDATE user
                    SET shared_data = ?
                    WHERE phone = ?
                ''', (
                    tunnel_info['bandwidth_usage'],
                    tunnel_info['sharer_phone']
                ))
                
                conn.commit()
                conn.close()
            
            # Handle poor health
            if health_status == 'poor':
                logger.warning(f"Tunnel {tunnel_id} ({instance_name}) has poor health, initiating recovery...")
                
                # In production, this would try to recover the tunnel
                # For now, we'll mark it as 'fair' and hope it gets better
                tunnel_info['health'] = 'fair'
            
            return True
        
        except Exception as e:
            logger.error(f"Error checking tunnel health: {str(e)}")
            return False
    
    def find_best_tunnel(self, client_phone):
        """Find the best available tunnel for a client"""
        try:
            # Get all active sharers from the database with available capacity
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT phone, limit_gb, shared_data
                FROM user
                WHERE role = 'sharer' AND sharing_active = 1 AND shared_data < limit_gb
            ''')
            available_sharers = cursor.fetchall()
            conn.close()
            
            if not available_sharers:
                logger.warning(f"No available sharers found for client {client_phone}")
                return None
            
            # Find the best sharer based on available capacity
            best_sharer = sorted(
                available_sharers, 
                key=lambda x: x['shared_data'] / x['limit_gb']
            )[0]
            
            # Find an active tunnel for this sharer or create a new one
            for tunnel_id, tunnel_info in self.active_tunnels.items():
                if tunnel_info['sharer_phone'] == best_sharer['phone'] and tunnel_info['health'] != 'poor':
                    logger.info(f"Found existing tunnel {tunnel_id} for client {client_phone}")
                    return tunnel_id
            
            # No existing tunnel, create a new one
            new_tunnel = self.create_tunnel(best_sharer['phone'])
            if new_tunnel:
                logger.info(f"Created new tunnel {new_tunnel['id']} for client {client_phone}")
                return new_tunnel['id']
            
            logger.error(f"Failed to find or create tunnel for client {client_phone}")
            return None
        
        except Exception as e:
            logger.error(f"Error finding best tunnel: {str(e)}")
            return None

# Run tunnel manager
if __name__ == "__main__":
    manager = TunnelManager()
    
    # Start the monitoring thread
    monitoring_thread = threading.Thread(target=manager.monitor_tunnels)
    monitoring_thread.daemon = True
    monitoring_thread.start()
    
    # Main loop to process API requests
    # In a real app, this would be an API server
    try:
        logger.info("Tunnel Manager started successfully.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down Tunnel Manager...")
        sys.exit(0)
