from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, BooleanField, IntegerField, ValidationError
from wtforms.validators import DataRequired, NumberRange, Regexp
import logging
import os
import pyotp
import requests
import json
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)

# Load configuration
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL',
        f'sqlite:///{os.path.join(os.path.dirname(os.path.abspath(__file__)), "netshare.db")}')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

app.config.from_object(Config)

# Initialize SQLAlchemy with the app
db = SQLAlchemy(app)

# ======= Database Models =======

class User(db.Model):
    __tablename__ = 'user'
    phone = db.Column(db.String(10), primary_key=True)
    role = db.Column(db.String(10), nullable=False)
    limit_gb = db.Column(db.Integer, default=5)
    shared_data = db.Column(db.Float, default=0.0)  # Data shared today in GB
    last_reset = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    sharing_active = db.Column(db.Boolean, default=False)
    connection_id = db.Column(db.String(36), nullable=True)  # UUID for active connection

class Connection(db.Model):
    __tablename__ = 'connection'
    id = db.Column(db.String(36), primary_key=True)  # UUID
    sharer_phone = db.Column(db.String(10), db.ForeignKey('user.phone'))
    client_phone = db.Column(db.String(10), nullable=True)  # Can be null if no client connected
    fly_instance = db.Column(db.String(50), nullable=False)  # fly.io instance ID
    status = db.Column(db.String(20), default="active")  # active, paused, terminated
    bandwidth_used = db.Column(db.Float, default=0.0)  # in GB
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    last_active = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    # Relationship to sharer
    sharer = db.relationship('User', backref='connections')

# ======= Forms =======

class LoginForm(FlaskForm):
    phone = StringField('Phone Number', validators=[DataRequired(), Regexp(r'^\d{10}$', message="Phone number must be 10 digits")])
    submit = SubmitField('Send OTP')

class OTPForm(FlaskForm):
    otp = StringField('Enter OTP', validators=[DataRequired()])
    submit = SubmitField('Verify OTP')

class SignupForm(FlaskForm):
    phone = StringField('Phone Number', validators=[DataRequired(), Regexp(r'^\d{10}$', message="Phone number must be 10 digits")])
    role = StringField('Role (sharer/client)', validators=[DataRequired()])
    submit = SubmitField('Create Account')

    def validate_role(self, field):
        if field.data.lower() not in ['sharer', 'client']:
            raise ValidationError("Role must be 'sharer' or 'client'.")

    def validate_phone(self, field):
        # Using execute instead of direct get to avoid SQLAlchemy operational errors
        try:
            user = db.session.execute(db.select(User).filter_by(phone=field.data)).scalar_one_or_none()
            if user:
                raise ValidationError("Phone number already registered.")
        except Exception as e:
            logger.warning(f"Error validating phone: {str(e)}")
            # Validation will proceed without error - this way, the form can still submit
            # and database models can be created when needed

class SharerForm(FlaskForm):
    limit_gb = IntegerField('Max share per day (GB)', validators=[DataRequired(), NumberRange(min=1, max=100)])
    sharing = BooleanField('Sharing ON')
    submit = SubmitField('Save Settings')

class ClientForm(FlaskForm):
    connect = SubmitField('Connect to NetShare')
    disconnect = SubmitField('Disconnect')

# ======= Utils =======

def login_required(f):
    def decorated_function(*args, **kwargs):
        if 'phone' not in session:
            flash('Please log in first', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def get_user(phone):
    """Safely get a user by phone number"""
    try:
        return db.session.execute(db.select(User).filter_by(phone=phone)).scalar_one_or_none()
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}")
        return None

def reset_daily_usage():
    """Reset daily data usage for all users if last_reset was yesterday"""
    while True:
        try:
            with app.app_context():
                today = datetime.now(UTC).date()

                # Use more robust querying
                users = db.session.execute(
                    db.select(User).where(User.last_reset < today)
                ).scalars().all()

                for user in users:
                    user.shared_data = 0.0
                    user.last_reset = datetime.now(UTC)

                db.session.commit()
                logger.info(f"Reset daily usage for {len(users)} users")
        except Exception as e:
            logger.error(f"Error in reset_daily_usage: {str(e)}")

        # Sleep for 1 hour before next check
        time.sleep(3600)

# Fly.io integration utilities

class FlyNetworkManager:
    BASE_URL = os.getenv('FLY_API_URL', 'https://api.fly.io/v1')
    API_KEY = os.getenv('FLY_API_KEY', '')

    @classmethod
    def get_headers(cls):
        return {
            'Authorization': f'Bearer {cls.API_KEY}',
            'Content-Type': 'application/json'
        }

    @classmethod
    def create_tunnel(cls, user_id):
        """Create a new tunnel instance on fly.io"""
        try:
            # Generate a unique instance name based on user ID
            instance_name = f"netshare-{user_id}-{int(time.time())}"

            # This would be replaced with actual fly.io API calls
            # For now we simulate a successful creation
            logger.info(f"Creating fly.io tunnel for {user_id}: {instance_name}")

            # In a real implementation, you would:
            # 1. Create a new fly app instance
            # 2. Deploy a tunnel service to it
            # 3. Return connection details

            return {
                'success': True,
                'instance_id': instance_name,
                'proxy_url': f"https://{instance_name}.fly.dev",
                'tunnel_port': 5000 + hash(user_id) % 1000  # Simulate a port assignment
            }
        except Exception as e:
            logger.error(f"Error creating fly.io tunnel: {str(e)}")
            return {'success': False, 'error': str(e)}

    @classmethod
    def terminate_tunnel(cls, instance_id):
        """Terminate a fly.io tunnel instance"""
        try:
            # This would call the fly.io API to destroy the instance
            logger.info(f"Terminating fly.io tunnel: {instance_id}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Error terminating fly.io tunnel: {str(e)}")
            return {'success': False, 'error': str(e)}

    @classmethod
    def get_active_tunnels(cls):
        """Get list of active tunnels"""
        # In a real implementation, this would query the fly.io API
        try:
            active_tunnels = db.session.execute(
                db.select(Connection).filter_by(status='active')
            ).scalars().all()

            return {
                'success': True,
                'tunnels': [
                    {
                        'instance_id': conn.fly_instance,
                        'sharer_id': conn.sharer_phone,
                        'client_id': conn.client_phone,
                        'last_active': conn.last_active.isoformat(),
                        'bandwidth_used': conn.bandwidth_used
                    }
                    for conn in active_tunnels
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching active tunnels: {str(e)}")
            return {'success': False, 'error': str(e)}

# Background task to monitor network stability and switch connections if needed

def monitor_connections():
    """Monitor active connections and switch to more stable ones if needed"""
    while True:
        try:
            with app.app_context():
                # Get all active client connections
                clients = db.session.execute(
                    db.select(User).filter_by(role='client')
                ).scalars().all()

                for client in clients:
                    # If client has a connection_id, check its stability
                    if client.connection_id:
                        connection = db.session.execute(
                            db.select(Connection).filter_by(id=client.connection_id)
                        ).scalar_one_or_none()

                        if connection:
                            # Check if connection is still active and stable
                            # In a real implementation, this would involve ping tests, bandwidth checks, etc.
                            if connection.status != 'active' or (datetime.now(UTC) - connection.last_active).total_seconds() > 300:
                                # Connection unstable, find a better one
                                logger.info(f"Connection {connection.id} unstable for client {client.phone}, finding alternative")

                                # Find available sharers with capacity
                                available_sharers = db.session.execute(
                                    db.select(User).filter_by(
                                        role='sharer',
                                        sharing_active=True
                                    ).where(User.shared_data < User.limit_gb)
                                ).scalars().all()

                                if available_sharers:
                                    # Sort by least used capacity
                                    available_sharers.sort(key=lambda x: x.shared_data / x.limit_gb)
                                    best_sharer = available_sharers[0]

                                    # Create new connection
                                    switch_connection(client.phone, best_sharer.phone)
                                    logger.info(f"Switched client {client.phone} to sharer {best_sharer.phone}")
        except Exception as e:
            logger.error(f"Error in connection monitor: {str(e)}")

        # Sleep for 1 minute before next check
        time.sleep(60)

def switch_connection(client_phone, sharer_phone):
    """Switch a client's connection to a new sharer"""
    client = get_user(client_phone)
    sharer = get_user(sharer_phone)

    if not client or not sharer:
        return False

    # If client has existing connection, update it
    if client.connection_id:
        old_connection = db.session.execute(
            db.select(Connection).filter_by(id=client.connection_id)
        ).scalar_one_or_none()

        if old_connection:
            old_connection.status = 'terminated'
            old_connection.client_phone = None

    # Check if sharer has an active connection
    sharer_connections = db.session.execute(
        db.select(Connection).filter_by(
            sharer_phone=sharer.phone,
            status='active'
        )
    ).scalars().all()

    if not sharer_connections:
        # Create new tunnel for sharer
        tunnel_info = FlyNetworkManager.create_tunnel(sharer.phone)
        if not tunnel_info['success']:
            logger.error(f"Failed to create tunnel for sharer {sharer.phone}")
            return False

        # Create new connection record
        new_connection = Connection(
            id=str(uuid.uuid4()),
            sharer_phone=sharer.phone,
            client_phone=client.phone,
            fly_instance=tunnel_info['instance_id'],
            status='active',
            created_at=datetime.now(UTC),
            last_active=datetime.now(UTC)
        )
        db.session.add(new_connection)
        db.session.commit()

        # Update client's connection ID
        client.connection_id = new_connection.id
        db.session.commit()
        logger.info(f"Client {client.phone} connected to sharer {sharer.phone} via {tunnel_info['proxy_url']}")
        return True
    else:
        # Reuse existing sharer connection if it's active
        active_connection = sharer_connections[0]
        active_connection.client_phone = client.phone
        active_connection.last_active = datetime.now(UTC)
        db.session.commit()

        client.connection_id = active_connection.id
        db.session.commit()
        logger.info(f"Client {client.phone} reconnected to existing sharer connection {active_connection.id}")
        return True

# ======= Routes =======

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    form = SignupForm()
    if form.validate_on_submit():
        phone = form.phone.data
        role = form.role.data.lower()

        # Create new user in the database
        new_user = User(phone=phone, role=role)
        db.session.add(new_user)
        try:
            db.session.commit()
            logger.info(f"New user registered: {phone} as {role}")
            flash('Account created. You can now log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating user: {str(e)}")
            flash('Error creating account. Please try again.', 'danger')

    return render_template('signup.html', form=form)

@app.route('/', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        phone = form.phone.data
        user = get_user(phone)
        if user:
            secret = session.get('otp_secret')
            if not secret:
                secret = pyotp.random_base32()
                session['otp_secret'] = secret
            totp = pyotp.TOTP(secret)
            otp = totp.now()
            session['phone_tmp'] = phone
            # In production, send the OTP via SMS
            print(f"OTP for {phone}: {otp}")
            flash("OTP sent to your phone", "info")
            return redirect(url_for('verify_otp'))
        flash('Unknown phone number', 'danger')
    return render_template('login.html', form=form)

@app.route('/verify', methods=['GET', 'POST'])
def verify_otp():
    form = OTPForm()
    if form.validate_on_submit():
        otp = form.otp.data
        secret = session.get('otp_secret')
        phone = session.get('phone_tmp')
        totp = pyotp.TOTP(secret)

        if totp.verify(otp, valid_window=1):  # allows +/- 30 seconds (1 window)
            session['phone'] = phone  # Set the phone in the session
            session.pop('otp_secret', None)
            session.pop('phone_tmp', None)
            flash('OTP verified. Logged in.', 'success')
            logger.info(f"OTP verified for {phone}. Redirecting to dashboard.")
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid OTP. Try again.', 'danger')
            logger.warning(f"Invalid OTP for {phone}")

    return render_template('verify.html', form=form)

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    phone = session.get('phone')
    user = get_user(phone)

    if not user:
        flash('User not found in database.', 'danger')
        return redirect(url_for('logout'))

    role = user.role

    if role == 'sharer':
        form = SharerForm(limit_gb=user.limit_gb, sharing=user.sharing_active)
        if form.validate_on_submit():
            # Update user settings
            user.limit_gb = form.limit_gb.data

            # Handle sharing toggle
            if form.sharing.data != user.sharing_active:
                if form.sharing.data:
                    # Start sharing
                    tunnel_info = FlyNetworkManager.create_tunnel(user.phone)
                    if tunnel_info['success']:
                        # Create new connection record
                        new_connection = Connection(
                            id=str(uuid.uuid4()),
                            sharer_phone=user.phone,
                            fly_instance=tunnel_info['instance_id'],
                            status='active'
                        )
                        db.session.add(new_connection)
                        user.sharing_active = True
                        db.session.commit()
                        flash('Sharing started. Your bandwidth is now available in the NetShare pool.', 'success')
                    else:
                        flash(f'Failed to start sharing: {tunnel_info.get("error", "Unknown error")}', 'danger')
                else:
                    # Stop sharing - terminate all active connections
                    active_connections = db.session.execute(
                        db.select(Connection).filter_by(
                            sharer_phone=user.phone,
                            status='active'
                        )
                    ).scalars().all()

                    for conn in active_connections:
                        # Disconnect any clients using this connection
                        if conn.client_phone:
                            client = get_user(conn.client_phone)
                            if client and client.connection_id == conn.id:
                                client.connection_id = None

                        # Terminate fly.io instance
                        FlyNetworkManager.terminate_tunnel(conn.fly_instance)
                        conn.status = 'terminated'

                    user.sharing_active = False
                    db.session.commit()
                    flash('Sharing stopped.', 'info')
            else:
                db.session.commit()
                flash('Settings updated', 'success')

        # Get current usage data
        active_connections = db.session.execute(
            db.select(Connection).filter_by(
                sharer_phone=user.phone,
                status='active'
            )
        ).scalars().all()

        total_bandwidth = sum(conn.bandwidth_used for conn in active_connections)
        connected_clients = sum(1 for conn in active_connections if conn.client_phone)

        return render_template(
            'sharer.html',
            form=form,
            phone=phone,
            user=user,
            total_bandwidth=total_bandwidth,
            connected_clients=connected_clients,
            active_connections=active_connections
        )

    elif role == 'client':
        form = ClientForm()

        # Check if client already has a connection
        active_connection = None
        if user.connection_id:
            active_connection = db.session.execute(
                db.select(Connection).filter_by(id=user.connection_id)
            ).scalar_one_or_none()

        if form.validate_on_submit():
            if 'connect' in request.form:
                # Find available sharers with capacity
                available_sharers = db.session.execute(
                    db.select(User).filter_by(
                        role='sharer',
                        sharing_active=True
                    ).where(User.shared_data < User.limit_gb)
                ).scalars().all()

                if available_sharers:
                    # Sort by least used capacity
                    available_sharers.sort(key=lambda x: x.shared_data / x.limit_gb)
                    best_sharer = available_sharers[0]

                    # Connect to the best sharer
                    connection_success = switch_connection(user.phone, best_sharer.phone)

                    if connection_success:
                        flash('Connected! Enjoy your NetShare bandwidth.', 'success')
                        logger.info(f"Client {phone} connected to NetShare via sharer {best_sharer.phone}")
                    else:
                        flash('Failed to connect. Please try again.', 'danger')
                else:
                    flash('No available sharers found. Please try again later.', 'warning')

            elif 'disconnect' in request.form and active_connection:
                # Disconnect from current connection
                active_connection.client_phone = None
                user.connection_id = None
                db.session.commit()
                flash('Disconnected from NetShare.', 'info')

        # Refresh connection status after form submission
        if user.connection_id:
            active_connection = db.session.execute(
                db.select(Connection).filter_by(id=user.connection_id)
            ).scalar_one_or_none()

        return render_template(
            'client.html',
            form=form,
            phone=phone,
            user=user,
            connection=active_connection
        )

    else:
        flash('Invalid user role', 'danger')
        return redirect(url_for('logout'))

@app.route('/logout')
def logout():
    phone = session.get('phone')
    if phone:
        logger.info(f"User logout: {phone}")
    session.clear()
    flash('Successfully logged out', 'info')
    return redirect(url_for('login'))

@app.route('/api/connections/status', methods=['GET'])
@login_required
def connection_status():
    """API endpoint to get connection status"""
    phone = session.get('phone')
    user = get_user(phone)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    if user.role == 'client':
        # Return client connection status
        connection = None
        if user.connection_id:
            conn = db.session.execute(
                db.select(Connection).filter_by(id=user.connection_id)
            ).scalar_one_or_none()

            if conn:
                connection = {
                    'id': conn.id,
                    'sharer_phone': conn.sharer_phone,
                    'status': conn.status,
                    'bandwidth_used': conn.bandwidth_used,
                    'proxy_url': f"https://{conn.fly_instance}.fly.dev"
                }

        return jsonify({
            'role': 'client',
            'phone': user.phone,
            'connection': connection
        })

    elif user.role == 'sharer':
        # Return sharer connections
        active_connections = db.session.execute(
            db.select(Connection).filter_by(
                sharer_phone=user.phone,
                status='active'
            )
        ).scalars().all()

        connections = [{
            'id': conn.id,
            'client_phone': conn.client_phone,
            'bandwidth_used': conn.bandwidth_used,
            'created_at': conn.created_at.isoformat(),
            'last_active': conn.last_active.isoformat()
        } for conn in active_connections]

        return jsonify({
            'role': 'sharer',
            'phone': user.phone,
            'sharing_active': user.sharing_active,
            'limit_gb': user.limit_gb,
            'shared_data': user.shared_data,
            'connections': connections
        })

    return jsonify({'error': 'Invalid user role'}), 400

@app.route('/api/network/available', methods=['GET'])
@login_required
def available_networks():
    """API endpoint to get available sharers"""
    phone = session.get('phone')
    user = get_user(phone)

    if not user or user.role != 'client':
        return jsonify({'error': 'Unauthorized'}), 403

    # Find available sharers with capacity
    available_sharers = db.session.execute(
        db.select(User).filter_by(
            role='sharer',
            sharing_active=True
        ).where(User.shared_data < User.limit_gb)
    ).scalars().all()

    networks = [{
        'sharer_id': sharer.phone[-4:],  # Last 4 digits for privacy
        'available_gb': sharer.limit_gb - sharer.shared_data,
        'signal_quality': 'good' if (sharer.limit_gb - sharer.shared_data) > 2 else 'fair'
    } for sharer in available_sharers]

    return jsonify({
        'networks': networks
    })

# ======= Error Handlers =======

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error='Page not found'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('error.html', error='Internal server error'), 500

# ======= Initialize Database =======
def init_db():
    """Initialize the database and create tables"""
    with app.app_context():
        try:
            # Get database path from config
            db_uri = app.config['SQLALCHEMY_DATABASE_URI']
            if db_uri.startswith('sqlite:///'):
                # Extract path from SQLite URI
                db_path = db_uri.replace('sqlite:///', '')
                # If path is relative, make it absolute
                if not os.path.isabs(db_path):
                    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
                # Remove existing database if it exists
                if os.path.exists(db_path):
                    os.remove(db_path)
                    logger.info(f"Removed existing database: {db_path}")

            # Create all tables fresh
            db.create_all()

            # Add verification step to make sure tables created properly
            inspector = db.inspect(db.engine)
            table_names = inspector.get_table_names()

            # Verify tables created correctly
            if 'user' not in table_names or 'connection' not in table_names:
                raise RuntimeError(f"Tables not created correctly. Found: {table_names}")

            # For each table, verify columns
            user_columns = [col['name'] for col in inspector.get_columns('user')]
            required_user_cols = ['phone', 'role', 'limit_gb', 'shared_data', 'last_reset',
                                 'sharing_active', 'connection_id']

            connection_columns = [col['name'] for col in inspector.get_columns('connection')]
            required_conn_cols = ['id', 'sharer_phone', 'client_phone', 'fly_instance',
                                 'status', 'bandwidth_used', 'created_at', 'last_active']

            missing_user_cols = [col for col in required_user_cols if col not in user_columns]
            missing_conn_cols = [col for col in required_conn_cols if col not in connection_columns]

            if missing_user_cols:
                raise RuntimeError(f"Missing user columns: {missing_user_cols}")
            if missing_conn_cols:
                raise RuntimeError(f"Missing connection columns: {missing_conn_cols}")

            logger.info(f"Database tables created successfully. Tables: {', '.join(table_names)}")
            logger.info(f"User columns: {', '.join(user_columns)}")
            logger.info(f"Connection columns: {', '.join(connection_columns)}")

        except Exception as e:
            logger.error(f"Error initializing database: {str(e)}")
            raise

# ======= Run App =======
if __name__ == '__main__':
    # Initialize the database before running the app
    init_db()

    # Start background tasks as daemon threads
    reset_thread = threading.Thread(target=reset_daily_usage, daemon=True)
    reset_thread.start()

    monitor_thread = threading.Thread(target=monitor_connections, daemon=True)
    monitor_thread.start()

    # Run the Flask application
    debug_mode = os.getenv('FLASK_ENV') != 'production'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))