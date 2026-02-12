import os
from flask_mysqldb import MySQL
from apscheduler.schedulers.background import BackgroundScheduler
from google import genai
from dotenv import load_dotenv

load_dotenv()

mysql = MySQL()
scheduler = BackgroundScheduler()

# Initialize Gemini Client
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    client = None
else:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")
        client = None