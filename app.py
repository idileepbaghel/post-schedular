import io
import os
from flask import Flask, render_template, request, flash, redirect, url_for, session, jsonify
from dotenv import load_dotenv
from flask_mysqldb import MySQL
from datetime import datetime, timedelta
import markdown
from google import genai
import base64
from google.genai import types
from PIL import Image, ImageDraw
import base64
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import re
from functools import wraps
import sys
import traceback
import json
import urllib.parse
import secrets

load_dotenv()

app = Flask(__name__)

# Database configuration
# for development
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'learntrail_content' 
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

# for production (uncomment when deploying)
# app.config['MYSQL_HOST'] = 'localhost'
# app.config['MYSQL_USER'] = 'learntrail_dbcontent'
# app.config['MYSQL_PASSWORD'] = '(hmS-lZQYdsS.)MU'
# app.config['MYSQL_DB'] = 'learntrail_content'
# app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)
scheduler = BackgroundScheduler()
app.secret_key = "dileep"

# LinkedIn OAuth Configuration
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
LINKEDIN_REDIRECT_URI = 'http://localhost:5500/linkedin/callback'

print(f"LinkedIn Config: {LINKEDIN_CLIENT_ID}, {LINKEDIN_CLIENT_SECRET}, {LINKEDIN_REDIRECT_URI}")

# Folder to save generated images
IMAGE_FOLDER = os.path.join('static', 'generated_image')
os.makedirs(IMAGE_FOLDER, exist_ok=True)

# Initialize Gemini client
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("FATAL: GEMINI_API_KEY environment variable not set.")
    client = None
else:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini client initialized successfully")
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")
        client = None

# ================================
# LOGIN REQUIRED DECORATOR
# ================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or 'linkedin_token' not in session:
            flash("🔒 Please sign in to access this page.", "warning")
            return redirect(url_for('signin'))
        return f(*args, **kwargs)
    return decorated_function

# ================================
# AUTHENTICATION ROUTES
# ================================

@app.route('/signin')
def signin():
    """Sign in page - redirects to main page if already logged in"""
    if 'user_id' in session and 'linkedin_token' in session:
        return redirect(url_for('generate_text'))
    return render_template('auth.html', page='signin')

@app.route('/signup')
def signup():
    """Sign up page - redirects to main page if already logged in"""
    if 'user_id' in session and 'linkedin_token' in session:
        return redirect(url_for('generate_text'))
    return render_template('auth.html', page='signup')

@app.route('/logout')
def logout():
    """Logout route - clears all session data"""
    session.clear()
    flash("✅ You have been logged out successfully.", "success")
    return redirect(url_for('signin'))

# ================================
# LINKEDIN OAUTH ROUTES
# ================================

@app.route('/linkedin/login')
def linkedin_login():
    """Initiates LinkedIn OAuth flow"""
    print("\n=== [DEBUG] /linkedin/login CALLED ===")

    if not LINKEDIN_CLIENT_ID:
        print("[ERROR] Missing LINKEDIN_CLIENT_ID environment variable")
        flash("⚠️ LinkedIn Client ID not configured.", "danger")
        return redirect(url_for('signin'))

    print(f"[DEBUG] LINKEDIN_CLIENT_ID: {LINKEDIN_CLIENT_ID}")
    print(f"[DEBUG] LINKEDIN_REDIRECT_URI: {LINKEDIN_REDIRECT_URI}")

    scopes = "openid profile email w_member_social"  
    print(f"[DEBUG] OAuth scopes: {scopes}")

    encoded_redirect = urllib.parse.quote(LINKEDIN_REDIRECT_URI, safe='')
    print(f"[DEBUG] Encoded redirect URI: {encoded_redirect}")

    state = secrets.token_urlsafe(16)
    session["linkedin_state"] = state

    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={encoded_redirect}"
        f"&scope={scopes}"
        f"&state={state}"
    )

    print("[DEBUG] Redirecting user to LinkedIn authorization URL:")
    print(f"[DEBUG] Generated state: {state}")
    print(f"[DEBUG] Auth URL: {auth_url}")
    print("=== [DEBUG] /linkedin/login END ===\n")

    return redirect(auth_url)

@app.route('/linkedin/callback')
def linkedin_callback():
    """Handles LinkedIn OAuth callback - creates or updates user"""
    print("\n=== [DEBUG] /linkedin/callback CALLED ===")
    
    print(f"[DEBUG] Request args: {dict(request.args)}")

    error = request.args.get("error")
    error_description = request.args.get("error_description")

    if error:
        print(f"[ERROR] LinkedIn login error: {error} | {error_description}")
        flash(f"❌ LinkedIn login error: {error} - {error_description}", "danger")
        return redirect(url_for('signin'))

    code = request.args.get("code")
    if not code:
        print("[ERROR] Missing authorization code in callback URL")
        flash("⚠️ LinkedIn authorization failed: no code provided.", "danger")
        return redirect(url_for('signin'))

    print(f"[DEBUG] Received authorization code: {code}")

    # Exchange code for access token
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET
    }

    print("[DEBUG] Sending POST request to LinkedIn token endpoint...")

    try:
        r = requests.post(token_url, data=data, timeout=10)
        print(f"[DEBUG] LinkedIn token response status: {r.status_code}")
        print(f"[DEBUG] LinkedIn token response body: {r.text}")
        r.raise_for_status()
        token_data = r.json()
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] LinkedIn HTTP error: {e}")
        flash(f"❌ Error getting LinkedIn access token: {r.text}", "danger")
        return redirect(url_for('signin'))
    except Exception as e:
        print(f"[ERROR] LinkedIn token request failed: {e}")
        flash(f"❌ Error getting LinkedIn access token: {str(e)}", "danger")
        return redirect(url_for('signin'))

    access_token = token_data.get("access_token")
    print(f"[DEBUG] Extracted access_token: {access_token}")

    if not access_token:
        print("[ERROR] Access token not found in LinkedIn response")
        flash("⚠️ Failed to retrieve access token from LinkedIn.", "danger")
        return redirect(url_for('signin'))

    # Fetch user profile
    try:
        profile_url = "https://api.linkedin.com/v2/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        print(f"[DEBUG] Fetching profile info from {profile_url}")
        profile_response = requests.get(profile_url, headers=headers, timeout=10)

        print(f"[DEBUG] Profile response code: {profile_response.status_code}")
        print(f"[DEBUG] Profile response text: {profile_response.text}")

        if profile_response.status_code == 200:
            profile_data = profile_response.json()
            user_sub = profile_data.get("sub")
            user_name = profile_data.get("name", "LinkedIn User")
            user_email = profile_data.get("email", "")
            
            print(f"[DEBUG] LinkedIn profile data: {profile_data}")
            print(f"[DEBUG] User sub (ID): {user_sub}")
            print(f"[DEBUG] User name: {user_name}")
            print(f"[DEBUG] User email: {user_email}")

            if not user_sub:
                print("[ERROR] User sub not found in profile")
                flash("⚠️ Could not retrieve user ID from LinkedIn.", "danger")
                return redirect(url_for('signin'))

            # Check if user exists in database
            try:
                cur = mysql.connection.cursor()
                cur.execute("SELECT * FROM linkedin_tokens WHERE user_urn=%s", (user_sub,))
                existing_user = cur.fetchone()

                if existing_user:
                    # User exists - UPDATE token (Sign In)
                    print(f"[DEBUG] Existing user found with ID: {existing_user['id']}")
                    cur.execute("""
                        UPDATE linkedin_tokens
                        SET access_token=%s, 
                            user_name=%s, 
                            user_email=%s,
                            updated_date=NOW(),
                            updated_by='System'
                        WHERE user_urn=%s
                    """, (access_token, user_name, user_email, user_sub))
                    mysql.connection.commit()
                    
                    user_id = existing_user['id']
                    print(f"[DEBUG] User signed in successfully. User ID: {user_id}")
                    flash(f"✅ Welcome back, {user_name}!", "success")
                else:
                    # New user - INSERT (Sign Up)
                    print(f"[DEBUG] New user - Creating account")
                    cur.execute("""
                        INSERT INTO linkedin_tokens 
                        (user_urn, access_token, user_name, user_email, added_by, added_date, created_at)
                        VALUES (%s, %s, %s, %s, 'System', NOW(), NOW())
                    """, (user_sub, access_token, user_name, user_email))
                    mysql.connection.commit()
                    user_id = cur.lastrowid
                    
                    print(f"[DEBUG] New user created with ID: {user_id}")
                    flash(f"✅ Welcome {user_name}! Your account has been created.", "success")

                cur.close()

                # Set session variables
                session['user_id'] = user_id
                session['linkedin_token'] = access_token
                session['linkedin_user'] = user_name
                session['linkedin_user_urn'] = user_sub
                session['user_email'] = user_email
                
                print(f"[DEBUG] Session set for user: {user_name} (ID: {user_id})")
                
            except Exception as db_error:
                print(f"[ERROR] Database error: {db_error}")
                print(traceback.format_exc())
                flash(f"❌ Database error: {str(db_error)}", "danger")
                return redirect(url_for('signin'))

        else:
            print(f"[ERROR] Failed to fetch LinkedIn profile: {profile_response.status_code}")
            
            # Try to extract user info from id_token as fallback
            id_token = token_data.get('id_token')
            if id_token:
                try:
                    parts = id_token.split('.')
                    if len(parts) >= 2:
                        payload = parts[1]
                        padding = 4 - (len(payload) % 4)
                        if padding != 4:
                            payload += '=' * padding
                        
                        decoded = base64.urlsafe_b64decode(payload)
                        token_info = json.loads(decoded)
                        print(f"[DEBUG] Decoded id_token payload: {token_info}")
                        
                        user_sub = token_info.get('sub')
                        user_name = token_info.get('name', 'LinkedIn User')
                        user_email = token_info.get('email', '')
                        
                        if user_sub:
                            print(f"[DEBUG] Found user ID in id_token: {user_sub}")
                            
                            try:
                                cur = mysql.connection.cursor()
                                cur.execute("SELECT * FROM linkedin_tokens WHERE user_urn=%s", (user_sub,))
                                existing_user = cur.fetchone()
                                
                                if existing_user:
                                    cur.execute("""
                                        UPDATE linkedin_tokens
                                        SET access_token=%s, user_name=%s, user_email=%s,
                                            updated_by='System', updated_date=NOW(), updated_date=NOW()
                                        WHERE user_urn=%s
                                    """, (access_token, user_name, user_email, user_sub))
                                    user_id = existing_user['id']
                                    flash(f"✅ Welcome back, {user_name}!", "success")
                                else:
                                    cur.execute("""
                                        INSERT INTO linkedin_tokens 
                                        (user_urn, access_token, user_name, user_email, added_by, added_date, created_at)
                                        VALUES (%s, %s, %s, %s, 'System', NOW(), NOW())
                                    """, (user_sub, access_token, user_name, user_email))
                                    user_id = cur.lastrowid
                                    flash(f"✅ Welcome {user_name}! Your account has been created.", "success")
                                
                                mysql.connection.commit()
                                cur.close()
                                
                                session['user_id'] = user_id
                                session['linkedin_token'] = access_token
                                session['linkedin_user'] = user_name
                                session['linkedin_user_urn'] = user_sub
                                session['user_email'] = user_email
                                
                                print(f"[DEBUG] Saved token from id_token for user_urn={user_sub}")
                            except Exception as e:
                                print(f"[WARNING] Could not save token from id_token: {e}")
                                flash("❌ Failed to retrieve LinkedIn profile.", "danger")
                                return redirect(url_for('signin'))
                        else:
                            flash("❌ Failed to retrieve LinkedIn profile.", "danger")
                            return redirect(url_for('signin'))
                except Exception as e:
                    print(f"[WARNING] Could not decode id_token: {e}")
                    flash("❌ Failed to retrieve LinkedIn profile.", "danger")
                    return redirect(url_for('signin'))
            else:
                flash("❌ Failed to retrieve LinkedIn profile.", "danger")
                return redirect(url_for('signin'))

    except Exception as e:
        print(f"[ERROR] Exception during profile fetch: {e}")
        print(traceback.format_exc())
        flash(f"❌ Error: {str(e)}", "danger")
        return redirect(url_for('signin'))

    print("=== [DEBUG] /linkedin/callback END - Redirecting to generate_text ===\n")
    return redirect(url_for('generate_text'))

# ================================
# SCHEDULED POSTS BACKGROUND JOB
# ================================

@app.route('/run_scheduled_posts')
def run_scheduled_posts():
    """Background job to post scheduled content to LinkedIn"""
    print(f"[{datetime.now()}] Checking for posts to publish...")

    try:
        with app.app_context():
            cursor = mysql.connection.cursor()

            cursor.execute("""
                SELECT * FROM scheduled_posts 
                WHERE DATE(post_date) = CURDATE() AND posted = 0 
            """)
            posts = cursor.fetchall()

            if not posts:
                print("No new posts to publish.")
                cursor.close()
                return jsonify({    
                    "success": True,
                    "message": "No new posts to publish.",
                    "posts_processed": 0
                }), 200

            print(f"Found {len(posts)} post(s) to publish.")

            successful_posts = []
            failed_posts = []

            for post in posts:
                post_id = post["id"]
                author_urn = post["author_urn"]
                content = post["content"]

                print(f"\nPreparing to post ID={post_id} for author={author_urn}")

                cursor.execute("SELECT access_token FROM linkedin_tokens WHERE user_urn = %s", (author_urn,))
                token_row = cursor.fetchone()

                if not token_row:
                    print(f"No access token found for author_urn={author_urn}, skipping...")
                    failed_posts.append({
                        "post_id": post_id,
                        "reason": "No access token found"
                    })
                    continue

                access_token = token_row["access_token"]

                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0"
                }

                data = {
                    "author": f"urn:li:person:{author_urn}",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": content},
                            "shareMediaCategory": "NONE"
                        }
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                    }
                }

                response = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=data)

                if response.status_code in [200, 201]:
                    print(f"Successfully posted ID={post_id}")

                    cursor.execute("""
                        UPDATE scheduled_posts 
                        SET posted = 1, 
                            posted_at = NOW(), 
                            updated_date = NOW(), 
                            updated_by = 'System'
                        WHERE id = %s
                    """, (post_id,))
                    mysql.connection.commit()
                    
                    successful_posts.append({
                        "post_id": post_id,
                        "author_urn": author_urn
                    })

                else:
                    print(f"Failed to post ID={post_id}: {response.status_code} - {response.text}")
                    failed_posts.append({
                        "post_id": post_id,
                        "status_code": response.status_code,
                        "error": response.text
                    })

            cursor.close()
            print("\nDone checking for scheduled posts.\n")
            
            return jsonify({
                "success": True,
                "message": "Scheduled posts processing completed.",
                "total_posts": len(posts),
                "successful": len(successful_posts),
                "failed": len(failed_posts),
                "successful_posts": successful_posts,
                "failed_posts": failed_posts
            }), 200

    except Exception as e:
        print(f"Error during scheduled posting: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "An error occurred during scheduled posting."
        }), 500

# ================================
# MAIN CONTENT GENERATION ROUTES
# ================================

@app.route('/', methods=['GET', 'POST'])
@login_required
def generate_text():
    """Main content generation page"""
    print("\n" + "=" * 100)
    print("=== [DEBUG] /generate_text ROUTE CALLED ===")
    print(f"[DEBUG] Request method: {request.method}")
    print(f"[DEBUG] User: {session.get('linkedin_user')} (ID: {session.get('user_id')})")
    print("=" * 100)

    if request.method == 'POST':
        if not session.get('linkedin_token'):
            flash("🔗 Please connect to LinkedIn first before generating content.", "warning")
            return redirect(url_for('linkedin_login'))

        print("\n[STEP 1] POST Request - Extracting Form Data...")
        content_length = request.form.get('content_length')
        content_schedule = request.form.get('content_schedule')
        start_date_str = request.form.get('start_date')
        purpose_goal = request.form.get('purpose_goal')
        target_audience = request.form.get('target_audience')
        tone_of_voice = request.form.get('tone_of_voice')
        formatting = request.form.get('formatting')
        topic_context = request.form.get('topic_context')
        keywords = request.form.get('keywords')
        cta = request.form.get('cta')
        hashtags = request.form.get('hashtags')
        user_prompt = request.form.get('prompt')

        # Validation
        if not client:
            flash("Gemini client not initialized.", "danger")
            return render_template('text_generation.html')

        if not all([content_length, content_schedule, topic_context, purpose_goal,
                    target_audience, tone_of_voice, start_date_str]):
            flash("Please fill in all required fields.", "warning")
            return render_template('text_generation.html')

        print("\n[STEP 2] All fields present - Generating day-wise content...")

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            daywise_content = []

            schedule_map = {
                "Single Day": 1,
                "2 Days": 2,
                "5 Days": 5,
                "10 Days": 10,
                "1 Week": 7,
                "2 Weeks": 14
            }
            num_days = schedule_map.get(content_schedule, 1)

            schedule_items = [start_date + timedelta(days=i) for i in range(num_days)]

            for scheduled_date in schedule_items:
                hashtags_instruction = hashtags.strip() if hashtags else ''

                system_message = (
                    "You are a professional LinkedIn content writer who understands tone, structure, and engagement psychology. "
                    "Create a single LinkedIn post that aligns with the user's provided details."
                )

                prompt_body = f"\n\nSCHEDULED DATE: {scheduled_date.strftime('%A, %B %d, %Y')}\n"
                prompt_body += f"Topic / Context: {topic_context}\n"
                prompt_body += f"Purpose / Goal: {purpose_goal}\n"
                prompt_body += f"Target Audience: {target_audience}\n"
                prompt_body += f"Tone of Voice: {tone_of_voice}\n"
                prompt_body += f"Formatting Preference: {formatting or 'Short and story format'}\n"
                if cta:
                    prompt_body += f"Optional Call-to-Action (CTA): {cta}\n"
                if keywords:
                    prompt_body += f"Keywords to Emphasize: {keywords}\n"
                if hashtags_instruction:
                    prompt_body += f"Hashtags (use these): {hashtags_instruction}\n"
                else:
                    prompt_body += "Hashtags: Please generate 4–6 relevant hashtags automatically at the end.\n"
                if user_prompt:
                    prompt_body += f"Additional Instructions: {user_prompt}\n"

                prompt_body += (
                    "\nRequirements:\n"
                    "- Keep the post under 3000 characters.\n"
                    "- Use an engaging hook, concise body (1–3 short paragraphs), and a CTA if relevant.\n"
                    "- Maintain natural LinkedIn tone and readability.\n"
                    "- Return final content as plain text only (no formatting tags or markdown).\n"
                )

                print(f"[DEBUG] Generating content for {scheduled_date.strftime('%Y-%m-%d')}...")

                max_output_tokens = (
                    800 if content_length.lower() == "short"
                    else 2000 if content_length.lower() == "medium"
                    else 3000
                )

                try:
                    response = client.models.generate_content(
                        model='gemini-2.0-flash-exp',
                        contents=f"{system_message}{prompt_body}",
                        config=types.GenerateContentConfig(
                            max_output_tokens=max_output_tokens,
                            temperature=0.7
                        )
                    )

                    if response and response.text:
                        text_output = response.text.strip()

                        for bad_phrase in [
                            "Here's a possible LinkedIn post, ready to copy and paste:",
                            "HASHTAGS:",
                            "Hashtags:",
                            "**HASHTAGS:**"
                        ]:
                            text_output = text_output.replace(bad_phrase, "")
                        text_output = text_output.strip()

                        html_output = markdown.markdown(text_output, extensions=['extra', 'smarty'])
                        print(f"[DEBUG] Generated for {scheduled_date.strftime('%Y-%m-%d')} ({len(text_output)} chars)")

                        daywise_content.append({
                            "date": scheduled_date.strftime("%Y-%m-%d"),
                            "text": text_output,
                            "html": html_output
                        })
                    else:
                        print(f"[WARN] Empty response for {scheduled_date.strftime('%Y-%m-%d')}")
                        daywise_content.append({
                            "date": scheduled_date.strftime("%Y-%m-%d"),
                            "text": "(No content generated)",
                            "html": "(No content generated)"
                        })

                except Exception as gen_err:
                    safe_error = str(gen_err).encode("utf-8", "ignore").decode("utf-8", "ignore")
                    print(f"[ERROR] Generation failed for {scheduled_date.strftime('%Y-%m-%d')}: {safe_error}")
                    daywise_content.append({
                        "date": scheduled_date.strftime("%Y-%m-%d"),
                        "text": "(Error generating this day's content)",
                        "html": "(Error generating this day's content)"
                    })

            session['daywise_content'] = daywise_content
            flash("Content generated successfully! Review your posts below.", "success")
            return render_template('daywise_preview.html', daywise_content=daywise_content)

        except Exception as e:
            safe_error = str(e).encode("utf-8", "ignore").decode("utf-8", "ignore")
            print(f"[ERROR] generate_text failed: {safe_error}")
            print(traceback.format_exc())
            flash("Error generating content. Please try again later.", "danger")
            return render_template('text_generation.html')

    if 'daywise_content' in session:
        return render_template('daywise_preview.html', daywise_content=session['daywise_content'])

    return render_template('text_generation.html')

@app.route('/clear_and_generate')
@login_required
def clear_and_generate():
    """Clear previous generation and start fresh"""
    if 'daywise_content' in session:
        session.pop('daywise_content')
    return redirect(url_for('generate_text'))

# ================================
# SCHEDULED POSTS MANAGEMENT
# ================================

@app.route('/add_post', methods=['GET', 'POST'])
@login_required
def add_post():
    """Manually add a scheduled post"""
    if request.method == 'POST':
        post_date = request.form.get('post_date')
        content = request.form.get('content')
        added_by = session.get('linkedin_user', 'Unknown User')

        if not post_date or not content:
            flash("⚠️ Please fill in all required fields.", "warning")
            return redirect(url_for('add_post'))

        try:
            cur = mysql.connection.cursor()
            cur.execute("""
                INSERT INTO scheduled_posts (post_date, content, added_by, author_urn, added_date)
                VALUES (%s, %s, %s, %s, NOW())
            """, (post_date, content, added_by, session.get('linkedin_user_urn')))
            mysql.connection.commit()
            cur.close()
            flash("✅ Post added successfully!", "success")
            return redirect(url_for('view_posts'))
        except Exception as e:
            print(f"[ERROR] Failed to add post: {e}")
            print(traceback.format_exc())
            flash(f"❌ Database Error: {str(e)}", "danger")

    return render_template('add_post.html')

# -------------------------------
# ROUTE: Save generated day-wise schedule to DB
# -------------------------------
from datetime import datetime

@app.route('/save_schedule', methods=['POST'])
def save_schedule():
    print("\n=== [DEBUG] /save_schedule ROUTE CALLED ===")
    print(f"[DEBUG] Session keys: {list(session.keys())}")

    if 'linkedin_token' not in session:
        flash("🔗 Please connect to LinkedIn first.", "warning")
        return redirect(url_for('linkedin_login'))

    # Get author_urn from session
    author_urn = session.get('linkedin_user_urn')
    print(f"[DEBUG] Author URN for saved posts: {author_urn}")

    if not author_urn:
        print("[WARNING] No author_urn in session")
        flash("⚠️ LinkedIn user ID not found. Please reconnect to LinkedIn.", "warning")
        return redirect(url_for('linkedin_login'))

    total_posts = int(request.form.get('total_posts', 0))
    added_by = "AI Generator"

    saved_count = 0
    for i in range(1, total_posts + 1):
        post_date = request.form.get(f'post_date_{i}')
        post_content = request.form.get(f'post_content_{i}')

        if post_date and post_content:
            try:
                # Current timestamp for added_date and updated_date
                now = datetime.now()

                cursor = mysql.connection.cursor()
                cursor.execute("""
                    INSERT INTO scheduled_posts 
                    (post_date, content, added_by, author_urn, posted, added_date, updated_by, updated_date)
                    VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)
                """, (post_date, post_content.strip(), added_by, author_urn, 0, now, now))
                mysql.connection.commit()
                cursor.close()
                saved_count += 1
                print(f"[DEBUG] Saved post with author_urn={author_urn} at {now}")
            except Exception as e:
                safe_error = str(e).encode("utf-8", "ignore").decode("utf-8", "ignore")
                print(f"[ERROR] Failed to save post: {safe_error}")
                flash(f"❌ Error saving post: {safe_error}", "danger")
                continue

    flash(f"✅ {saved_count} post(s) saved successfully!", "success")
    return redirect(url_for('view_posts'))



# -------------------------------
# ROUTE: View all scheduled posts
# -------------------------------
@app.route("/view_posts")
def view_posts():
    auth_urn = session.get('linkedin_user_urn')

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM scheduled_posts WHERE author_urn = %s", (auth_urn,))
    posts = cur.fetchall()
    cur.close()

    # Flatten posts into one list with proper date
    all_posts = []
    for post in posts:
        # Use scheduled_time if available, else post_date
        post_date = post.get("scheduled_time") or post.get("post_date")
        post["display_date"] = post_date
        all_posts.append(post)

    print("All Posts:", all_posts)

    return render_template("view_posts.html", all_posts=all_posts)


# -------------------------------
# ROUTE: Update existing post
# -------------------------------
@app.route('/update_post/<int:post_id>', methods=['GET', 'POST'])
def update_post(post_id):
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        post_date = request.form.get('post_date')
        content = request.form.get('content')
        updated_by = request.form.get('updated_by', 'Admin')

        try:
            cur.execute("""
                UPDATE scheduled_posts
                SET post_date=%s, content=%s, updated_by=%s, updated_date=NOW()
                WHERE id=%s
            """, (post_date, content, updated_by, post_id))
            mysql.connection.commit()
            cur.close()
            flash("✅ Post updated successfully!", "success")
            return redirect(url_for('view_posts'))
        except Exception as e:
            flash(f"❌ Update failed: {str(e)}", "danger")

    # GET request – fetch post to edit
    cur.execute("SELECT * FROM scheduled_posts WHERE id=%s", (post_id,))
    post = cur.fetchone()
    cur.close()

    if not post:
        flash("⚠️ Post not found.", "warning")
        return redirect(url_for('view_posts'))

    return render_template('update_post.html', post=post)


@app.route('/post_to_linkedin', methods=['POST'])
def post_to_linkedin():
    print("\n" + "="*80)
    print("=== [DEBUG] /post_to_linkedin ROUTE CALLED ===")
    print("="*80)
    
    # Check LinkedIn authentication
    access_token = session.get("linkedin_token")
    print(f"[DEBUG] Session keys: {list(session.keys())}")
    print(f"[DEBUG] LinkedIn token exists: {bool(access_token)}")
    
    if not access_token:
        print("[ERROR] No LinkedIn access token found")
        flash("⚠️ Please connect to LinkedIn first.", "warning")
        return redirect(url_for('generate_text'))
    
    print(f"[DEBUG] Access token (first 20 chars): {access_token[:20]}...")
    
    # Get content and post_id from form
    content = request.form.get('content', '').strip()
    post_id = request.form.get('post_id')
    
    print(f"[DEBUG] Content length: {len(content)} characters")
    print(f"[DEBUG] Content preview: {content[:100]}...")
    print(f"[DEBUG] Post ID: {post_id}")
    
    if not content:
        print("[ERROR] No content provided")
        print(f"[DEBUG] Form keys: {list(request.form.keys())}")
        flash("⚠️ No content to post.", "warning")
        return redirect(url_for('view_posts'))
    
    if len(content) > 3000:
        print(f"[WARNING] Content exceeds LinkedIn limit: {len(content)} chars")
        flash(f"⚠️ Content is too long ({len(content)} chars). LinkedIn limit is 3000.", "warning")
        return redirect(url_for('view_posts'))
    
    try:
        # Step 1: Get user profile using OpenID Connect
        print("\n[STEP 1] Fetching LinkedIn User Profile...")
        profile_url = "https://api.linkedin.com/v2/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        print(f"[DEBUG] GET {profile_url}")
        profile_response = requests.get(profile_url, headers=headers, timeout=10)
        
        print(f"[DEBUG] Profile response status: {profile_response.status_code}")
        print(f"[DEBUG] Profile response body: {profile_response.text}")
        
        if profile_response.status_code != 200:
            print(f"[ERROR] Failed to fetch LinkedIn profile")
            
            if profile_response.status_code == 401:
                print("[ERROR] Token expired - clearing session")
                session.pop('linkedin_token', None)
                flash("❌ LinkedIn session expired. Please reconnect.", "danger")
            elif profile_response.status_code == 403:
                print("[ERROR] Permission denied")
                flash("❌ Permission denied. Please reconnect to LinkedIn with proper permissions.", "danger")
                session.pop('linkedin_token', None)
            else:
                flash(f"❌ Failed to get LinkedIn profile.", "danger")
            
            return redirect(url_for('generate_text'))
        
        profile_data = profile_response.json()
        print(f"[DEBUG] Profile data keys: {list(profile_data.keys())}")
        print(f"[DEBUG] Full profile: {profile_data}")
        
        user_sub = profile_data.get("sub")
        print(f"[DEBUG] User ID (sub): {user_sub}")
        
        if not user_sub:
            print("[ERROR] 'sub' field not found in profile")
            flash("❌ Could not retrieve user ID from LinkedIn.", "danger")
            return redirect(url_for('generate_text'))
        
        # Step 2: Post using UGC Posts API (v2) - Most stable
        print("\n[STEP 2] Posting to LinkedIn UGC API...")
        post_url = "https://api.linkedin.com/v2/ugcPosts"
        headers_post = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0"
        }
        
        post_data = {
            "author": f"urn:li:person:{user_sub}",
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {
                        "text": content
                    },
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "CONNECTIONS"
            }
        }
        
        print(f"[DEBUG] POST {post_url}")
        import json
        print(f"[DEBUG] Headers: {headers_post}")
        print(f"[DEBUG] Payload:")
        print(json.dumps(post_data, indent=2))
        
        post_response = requests.post(
            post_url,
            headers=headers_post,
            json=post_data,
            timeout=10
        )
        
        print(f"[DEBUG] Post response status: {post_response.status_code}")
        print(f"[DEBUG] Post response headers: {dict(post_response.headers)}")
        print(f"[DEBUG] Post response body: {post_response.text}")
        
        # Step 3: Handle response
        if post_response.status_code in [200, 201]:
            print("[SUCCESS] ✅ Post created successfully on LinkedIn!")
            try:
                response_data = post_response.json()
                post_id = response_data.get('id', 'unknown')
                print(f"[DEBUG] Post ID: {post_id}")
            except:
                print("[DEBUG] Could not parse response JSON")
            
            flash("✅ Successfully posted to LinkedIn!", "success")
        else:
            print(f"[ERROR] ❌ Failed to create LinkedIn post")
            
            try:
                error_data = post_response.json()
                print(f"[ERROR] Error data: {error_data}")
                error_message = error_data.get('message', post_response.text)
            except:
                error_message = post_response.text
            
            if post_response.status_code == 401:
                print("[ERROR] Authentication failed")
                session.pop('linkedin_token', None)
                flash("❌ LinkedIn session expired. Please reconnect.", "danger")
            elif post_response.status_code == 403:
                print("[ERROR] Permission denied - missing w_member_social scope")
                flash("❌ Your LinkedIn app needs 'w_member_social' permission. Please reconnect.", "danger")
                session.pop('linkedin_token', None)
            elif post_response.status_code == 422:
                print("[ERROR] Invalid request data")
                flash(f"❌ Invalid post data: {error_message}", "danger")
            else:
                flash(f"❌ Failed to post: {error_message}", "danger")
            
    except requests.exceptions.Timeout as e:
        print(f"[ERROR] Request timeout: {e}")
        flash("❌ Request timed out. Please try again.", "danger")
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Request exception: {e}")
        flash(f"❌ Network error: {str(e)}", "danger")
    except Exception as e:
        print(f"[ERROR] Unexpected exception")
        print(f"[ERROR] Type: {type(e).__name__}")
        print(f"[ERROR] Message: {str(e)}")
        import traceback
        print(traceback.format_exc())
        flash(f"❌ Error: {str(e)}", "danger")
    
    print("="*80)
    print("=== [DEBUG] /post_to_linkedin ROUTE END ===")
    print("="*80 + "\n")
    
    return redirect(url_for('generate_text'))
    

@app.route('/generate_image', methods=['POST','GET'])
def generate_image():
    prompt = request.form.get('prompt', "").strip()
    if not prompt:
        return render_template('image_generation.html', error="⚠️ Please enter a prompt.")

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                candidate_count=1
            )
        )

        # parse response parts to find image part
        for part in response.candidates[0].content.parts:
            if part.inline_data:  # this is image data
                img = Image.open(io.BytesIO(part.inline_data.data))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                encoded = base64.b64encode(buf.getvalue()).decode('utf-8')
                return render_template('image_generation.html', image_data=encoded, prompt=prompt)

        # if no image found
        return render_template('image_generation.html', error="⚠️ Couldn't generate image.")

    except Exception as e:
        return render_template('image_generation.html', error=f"⚠️ Error: {str(e)}")

if __name__ == '__main__':
    app.run(debug=True, port=5500)