from flask import Flask
from extensions import mysql, scheduler
from config import config_dict # Import the config dictionary
import os

def create_app():
    app = Flask(__name__)
    
    # --- 1. Load Configuration ---
    # Determine environment: 'production' or 'development' (default)
    # You can set this in your OS: export FLASK_ENV=production
    env_name = os.environ.get('FLASK_ENV', 'default')
    
    # Load the config class based on the environment
    app.config.from_object(config_dict[env_name])
    
    # --- 2. Initialize Extensions ---
    mysql.init_app(app)
    
    if not scheduler.running:
        scheduler.start()

    # --- 3. Register Blueprints ---
    from app.routes.auth_routes import auth_bp
    from app.routes.content_routes import content_bp
    from app.routes.post_routes import post_bp
    from app.routes.scheduler_routes import scheduler_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(post_bp)
    app.register_blueprint(scheduler_bp)

    return app