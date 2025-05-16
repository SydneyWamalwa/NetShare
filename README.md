# NetShare Flask Application

A Flask web application for mobile data sharing between users.

## Setup

1. Create a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Create a .env file:
   ```
   cp .env.example .env
   ```
   Edit the .env file to set your SECRET_KEY and other configurations.

4. Run the application:
   ```
   flask run
   ```

5. Access the application at http://localhost:5000

## Features

- User login with phone number
- Different roles: Sharer and Client
- Sharers can configure data sharing limits
- Clients can connect to shared networks
- Responsive design with Bootstrap

## Production Deployment

For production deployment:
1. Set FLASK_ENV=production in .env
2. Use a proper WSGI server (Gunicorn, uWSGI)
3. Set up a database for user management
4. Use HTTPS with proper certificates
