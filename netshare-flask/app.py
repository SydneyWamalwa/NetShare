from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, BooleanField, IntegerField, ValidationError
from wtforms.validators import DataRequired, NumberRange, Regexp
from config import Config
import logging
import os
import pyotp
import requests
import json
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# ======= Database Models =======
class User(db.Model):
    phone = db.Column(db.String(10), primary_key=True)
    role = db.Column(db.String(10), nullable=False)
    limit_gb = db.Column(db.Integer, default=5)
    shared_data = db.Column(db.Float, default=0.0)  # Data shared today in GB
    last_reset = db.Column(db.DateTime, default=datetime.utcnow)
    sharing_active = db.Column(db.Boolean, default=False)
    connection_id = db.Column(db.String(36), nullable=True)  # UUID for active connection

class Connection(db.Model):
    id = db.Column(db.String(36), primary_key=True)  # UUID
    sharer_phone = db.Column(db.String(10), db.ForeignKey('user.phone'))
    client_phone = db.Column(db.String(10), nullable=True)  # Can be null if no client connected
    fly_instance = db.Column(db.String(50), nullable=False)  # fly.io instance ID
    status = db.Column(db.String(20), default="active")  # active, paused, terminated
    bandwidth_used = db.Column(db.Float, default=0.0)  # in GB
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)

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

    def validate_role(form, field):
        if field.data.lower() not in ['sharer', 'client']:
            raise ValidationError("Role must be 'sharer' or 'client'.")

    def validate_phone(form, field):
        if User.query.get(field.data):
            raise ValidationError("Phone number already registered.")

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

def reset_daily_usage():
    """Reset daily data usage for all users if last_reset was yesterday"""
    with app.app_context():
        today = datetime.utcnow().date()
        users = User.query.filter(User.last_reset < today).all()
        for user in users:
            user.shared_data = 0.0
            user.last_reset = datetime.utcnow()
        db.session.commit()
        logger.info(f"Reset daily usage for {len(users)} users")

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
            active_tunnels = Connection.query.filter_by(status='active').all()
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
                clients = User.query.filter_by(role='client').all()
                for client in clients:
                    # If client has a connection_id, check its stability
                    if client.connection_id:
                        connection = Connection.query.get(client.connection_id)
                        if connection:
                            # Check if connection is still active and stable
                            # In a real implementation, this would involve ping tests, bandwidth checks, etc.
                            if connection.status != 'active' or (datetime.utcnow() - connection.last_active).total_seconds() > 300:
                                # Connection unstable, find a better one
                                logger.info(f"Connection {connection.id} unstable for client {client.phone}, finding alternative")

                                # Find available sharers with capacity
                                available_sharers = User.query.filter_by(
                                    role='sharer',
                                    sharing_active=True
                                ).filter(
                                    User.shared_data < User.limit_gb
                                ).all()

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
    client = User.query.get(client_phone)
    sharer = User.query.get(sharer_phone)

    if not client or not sharer:
        return False

    # If client has existing connection, update it
    if client.connection_id:
        old_connection = Connection.query.get(client.connection_id)
        if old_connection:
            old_connection.status = 'terminated'
            old_connection.client_phone = None

    # Check if sharer has an active connection
    sharer_connections = Connection.query.filter_by(
        sharer_phone=sharer.phone,
        status='active'
    ).all()

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
            status='active'
        )
        db.session.add(new_connection)
    else:
        # Use existing sharer connection
        new_connection = sharer_connections[0]
        new_connection.client_phone = client.phone
        new_connection.last_active = datetime.utcnow()

    # Update client's connection_id
    client.connection_id = new_connection.id
    db.session.commit()
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
        db.session.commit()

        logger.info(f"New user registered: {phone} as {role}")
        flash('Account created. You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', form=form)

@app.route('/', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        phone = form.phone.data
        user = User.query.get(phone)
        if user:
            secret = session.get('otp_secret')
            if not secret:
                secret = pyotp.random_base32()
                session['otp_secret'] = secret
            else:
                secret = session['otp_secret']
            session['phone_tmp'] = phone
            totp = pyotp.TOTP(secret)
            otp = totp.now()
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
    user = User.query.get(phone)

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
                        flash('Sharing started. Your bandwidth is now available in the NetShare pool.', 'success')
                    else:
                        flash(f'Failed to start sharing: {tunnel_info.get("error", "Unknown error")}', 'danger')
                else:
                    # Stop sharing - terminate all active connections
                    active_connections = Connection.query.filter_by(
                        sharer_phone=user.phone,
                        status='active'
                    ).all()

                    for conn in active_connections:
                        # Disconnect any clients using this connection
                        if conn.client_phone:
                            client = User.query.get(conn.client_phone)
                            if client and client.connection_id == conn.id:
                                client.connection_id = None

                        # Terminate fly.io instance
                        FlyNetworkManager.terminate_tunnel(conn.fly_instance)
                        conn.status = 'terminated'

                    user.sharing_active = False
                    flash('Sharing stopped.', 'info')

            db.session.commit()
            flash('Settings updated', 'success')

        # Get current usage data
        active_connections = Connection.query.filter_by(
            sharer_phone=user.phone,
            status='active'
        ).all()

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
            active_connection = Connection.query.get(user.connection_id)

        if form.validate_on_submit():
            if 'connect' in request.form:
                # Find available sharers with capacity
                available_sharers = User.query.filter_by(
                    role='sharer',
                    sharing_active=True
                ).filter(
                    User.shared_data < User.limit_gb
                ).all()

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
            active_connection = Connection.query.get(user.connection_id)

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
    user = User.query.get(phone)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    if user.role == 'client':
        # Return client connection status
        connection = None
        if user.connection_id:
            conn = Connection.query.get(user.connection_id)
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
        active_connections = Connection.query.filter_by(
            sharer_phone=user.phone,
            status='active'
        ).all()

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
    user = User.query.get(phone)

    if not user or user.role != 'client':
        return jsonify({'error': 'Unauthorized'}), 403

    # Find available sharers with capacity
    available_sharers = User.query.filter_by(
        role='sharer',
        sharing_active=True
    ).filter(
        User.shared_data < User.limit_gb
    ).all()

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

# ======= Run App =======
if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    # Start background tasks
    reset_thread = threading.Thread(target=reset_daily_usage)
    reset_thread.daemon = True
    reset_thread.start()

    monitor_thread = threading.Thread(target=monitor_connections)
    monitor_thread.daemon = True
    monitor_thread.start()

    debug_mode = os.getenv('FLASK_ENV') != 'production'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))