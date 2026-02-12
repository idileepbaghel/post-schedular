import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY') or "dileep"
    MYSQL_CURSORCLASS = 'DictCursor'
    
    # LinkedIn Config (Shared)
    LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID")
    LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET")
    # Default to localhost for dev, can be overridden in .env
    LINKEDIN_REDIRECT_URI = os.environ.get("LINKEDIN_REDIRECT_URI", 'http://localhost:5500/linkedin/callback')
    # LINKEDIN_REDIRECT_URI = os.environ.get("LINKEDIN_REDIRECT_URI", 'https://connect.learntrail.co.in/linkedin/callback')

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    ENV = 'development'
    
    # Database - Development Credentials
    MYSQL_HOST = 'localhost'
    MYSQL_USER = 'root'
    MYSQL_PASSWORD = ''
    MYSQL_DB = 'learntrail_content'

class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    ENV = 'production'
    
    # Database - Production Credentials
    MYSQL_HOST = 'localhost'
    MYSQL_USER = 'learntrail_dbcontent'
    MYSQL_PASSWORD = '(hmS-lZQYdsS.)MU'
    MYSQL_DB = 'learntrail_content'
    
    # In production, ensure these are set!
    LINKEDIN_REDIRECT_URI = 'https://connect.learntrail.co.in/linkedin/callback'

# Dictionary to map environment names to classes
config_dict = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}