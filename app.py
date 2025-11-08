import io
import os
from flask import Flask, render_template, request, flash, redirect, url_for, session
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
import atexit

load_dotenv()

app = Flask(__name__)

# for development
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'learntrail_content' 
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)

scheduler = BackgroundScheduler()

app.secret_key = "dileep"

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
LINKEDIN_REDIRECT_URI = 'http://localhost:5500/linkedin/callback'

print(LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, LINKEDIN_REDIRECT_URI)

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

@app.route('/linkedin/login')
def linkedin_login():
    print("\n=== [DEBUG] /linkedin/login CALLED ===")

    if not LINKEDIN_CLIENT_ID:
        print("[ERROR] Missing LINKEDIN_CLIENT_ID environment variable")
        flash("⚠️ LinkedIn Client ID not configured.", "danger")
        return redirect(url_for('generate_text'))

    print(f"[DEBUG] LINKEDIN_CLIENT_ID: {LINKEDIN_CLIENT_ID}")
    print(f"[DEBUG] LINKEDIN_REDIRECT_URI: {LINKEDIN_REDIRECT_URI}")

    # FIXED: Use only the scopes your app has access to
    # Most LinkedIn apps have access to 'openid', 'profile', 'email'
    scopes = "openid profile email w_member_social"  
    print(f"[DEBUG] OAuth scopes: {scopes}")

    import urllib.parse
    encoded_redirect = urllib.parse.quote(LINKEDIN_REDIRECT_URI, safe='')
    print(f"[DEBUG] Encoded redirect URI: {encoded_redirect}")
    import secrets
    from urllib.parse import quote

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
    print("auth-url", auth_url)
    print("=== [DEBUG] /linkedin/login END ===\n")

    return redirect(auth_url)

@app.route('/linkedin/callback')
def linkedin_callback():
    print("\n=== [DEBUG] /linkedin/callback CALLED ===")
    
    # Log raw query parameters
    print(f"[DEBUG] Request args: {dict(request.args)}")

    error = request.args.get("error")
    error_description = request.args.get("error_description")

    if error:
        print(f"[ERROR] LinkedIn login error: {error} | {error_description}")
        flash(f"❌ LinkedIn login error: {error} - {error_description}", "danger")
        return redirect(url_for('generate_text'))

    code = request.args.get("code")
    if not code:
        print("[ERROR] Missing authorization code in callback URL")
        flash("⚠️ LinkedIn authorization failed: no code provided.", "danger")
        return redirect(url_for('generate_text'))

    print(f"[DEBUG] Received authorization code: {code}")

    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET
    }

    print("[DEBUG] Sending POST request to LinkedIn token endpoint...")
    print(f"[DEBUG] Token URL: {token_url}")
    print(f"[DEBUG] Payload: {data}")

    try:
        r = requests.post(token_url, data=data, timeout=10)
        print(f"[DEBUG] LinkedIn token response status: {r.status_code}")
        print(f"[DEBUG] LinkedIn token response body: {r.text}")
        r.raise_for_status()
        token_data = r.json()
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] LinkedIn HTTP error: {e}")
        flash(f"❌ Error getting LinkedIn access token: {r.text}", "danger")
        return redirect(url_for('generate_text'))
    except Exception as e:
        print(f"[ERROR] LinkedIn token request failed: {e}")
        flash(f"❌ Error getting LinkedIn access token: {str(e)}", "danger")
        return redirect(url_for('generate_text'))

    access_token = token_data.get("access_token")
    print(f"[DEBUG] Extracted access_token: {access_token}")

    if not access_token:
        print("[ERROR] Access token not found in LinkedIn response")
        flash("⚠️ Failed to retrieve access token from LinkedIn.", "danger")
        return redirect(url_for('generate_text'))

    # Save token to session
    session["linkedin_token"] = access_token
    print("[DEBUG] Access token stored in session")

    # Fetch user profile (optional)
    try:
        profile_url = "https://api.linkedin.com/v2/me"
        headers = {"Authorization": f"Bearer {access_token}"}
        print(f"[DEBUG] Fetching profile info from {profile_url}")
        profile_response = requests.get(profile_url, headers=headers, timeout=10)

        print(f"[DEBUG] Profile response code: {profile_response.status_code}")
        print(f"[DEBUG] Profile response text: {profile_response.text}")

        if profile_response.status_code == 200:
            profile_data = profile_response.json()
            user_name = profile_data.get("name", "User")
            print(f"[DEBUG] LinkedIn profile data parsed: {profile_data}")
            print(f"[DEBUG] Extracted user name: {user_name}")
            session["linkedin_user"] = user_name
            # try to capture the member id (used for urn:li:person:{id})
            user_sub = profile_data.get("id") or profile_data.get("sub")
            if user_sub:
                session["linkedin_user_urn"] = user_sub
                print(f"[DEBUG] Extracted user URN: {user_sub}")
                # persist token for background scheduler to use (upsert)
                try:
                    cur = mysql.connection.cursor()
                    cur.execute("SELECT id FROM linkedin_tokens WHERE user_urn=%s", (user_sub,))
                    existing = cur.fetchone()
                    if existing:
                        cur.execute("""
                            UPDATE linkedin_tokens
                            SET access_token=%s, updated_at=NOW()
                            WHERE user_urn=%s
                        """, (access_token, user_sub))
                    else:
                        cur.execute("""
                            INSERT INTO linkedin_tokens (user_urn, access_token, created_at)
                            VALUES (%s, %s, NOW())
                        """, (user_sub, access_token))
                    mysql.connection.commit()
                    cur.close()
                    print(f"[DEBUG] Saved linkedin token for user_urn={user_sub} to DB")
                except Exception as e:
                    print(f"[WARNING] Could not persist linkedin token to DB: {e}")
        else:
            print(f"[WARNING] Failed to fetch user profile. Code: {profile_response.status_code}")
            # Try to extract user info from id_token as fallback
            id_token = token_data.get('id_token')
            if id_token:
                try:
                    import json, base64
                    # Split the token and get the payload part
                    parts = id_token.split('.')
                    if len(parts) >= 2:
                        payload = parts[1]
                        # Add padding if needed
                        padding = 4 - (len(payload) % 4)
                        if padding != 4:
                            payload += '=' * padding
                        
                        decoded = base64.urlsafe_b64decode(payload)
                        token_data = json.loads(decoded)
                        print(f"[DEBUG] Decoded id_token payload: {token_data}")
                        
                        # Try to get user ID from token
                        user_sub = token_data.get('sub')
                        if user_sub:
                            print(f"[DEBUG] Found user ID in id_token: {user_sub}")
                            session["linkedin_user_urn"] = user_sub
                            try:
                                cur = mysql.connection.cursor()
                                cur.execute("SELECT id FROM linkedin_tokens WHERE user_urn=%s", (user_sub,))
                                existing = cur.fetchone()
                                if existing:
                                    cur.execute("""
                                        UPDATE linkedin_tokens
                                        SET access_token=%s, updated_by='System', updated_date=NOW()
                                        WHERE user_urn=%s
                                    """, (access_token, user_sub))
                                else:
                                    cur.execute("""
                                        INSERT INTO linkedin_tokens 
                                        (user_urn, access_token, added_by, added_date)
                                        VALUES (%s, %s, 'System', NOW())
                                    """, (user_sub, access_token))
                                mysql.connection.commit()
                                cur.close()
                                print(f"[DEBUG] Saved linkedin token from id_token for user_urn={user_sub}")
                            except Exception as e:
                                print(f"[WARNING] Could not save token from id_token: {e}")
                except Exception as e:
                    print(f"[WARNING] Could not decode id_token: {e}")

    except Exception as e:
        print(f"[ERROR] Exception during profile fetch: {e}")

    flash("✅ LinkedIn connected successfully!", "success")
    print("=== [DEBUG] /linkedin/callback END ===\n")
    return redirect(url_for('generate_text'))

@app.route('/', methods=['GET', 'POST'])
def generate_text():
    print("\n" + "="*100)
    print("=== [DEBUG] /generate_text ROUTE CALLED ===")
    print(f"[DEBUG] Request method: {request.method}")
    print("="*100)

    generated_text_html = None
    daywise_content = None
    user_prompt = None
    start_date_str = None
    num_days = 1

    if request.method == 'POST':
        # Require LinkedIn connection
        if not session.get('linkedin_token'):
            flash("🔗 Please connect to LinkedIn first before generating content.", "warning")
            return redirect(url_for('linkedin_login'))

        print("\n[STEP 1] POST Request - Extracting Form Data...")
        content_length = request.form.get('content_length')
        content_schedule = request.form.get('content_schedule')
        user_prompt = request.form.get('prompt')
        topic_context = request.form.get('topic_context')
        purpose_goal = request.form.get('purpose_goal')
        target_audience = request.form.get('target_audience')
        tone_of_voice = request.form.get('tone_of_voice')
        cta = request.form.get('cta') or None  # optional now
        keywords = request.form.get('keywords')
        hashtags = request.form.get('hashtags')
        formatting = request.form.get('formatting')
        start_date_str = request.form.get('start_date')
        num_days = int(request.form.get('num_days', 1))

        # Validation
        if not client:
            flash("❌ Gemini client not initialized.", "danger")
        elif not all([content_length, content_schedule, topic_context, purpose_goal, target_audience, tone_of_voice, start_date_str]):
            flash("⚠️ Please fill in all required fields.", "warning")
        else:
            print("\n[STEP 2] All fields present - Generating day-wise content...")
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                daywise_content = []

                for i in range(num_days):
                    scheduled_date = start_date + timedelta(days=i)
                    hashtags_instruction = hashtags.strip() if hashtags else ''

                    system_message = (
                        "You are a professional LinkedIn content writer who understands tone, structure, and engagement psychology. "
                        "Create a single LinkedIn post that aligns with the user’s provided details."
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

                    print(f"\n[DEBUG] Generating content for {scheduled_date.strftime('%Y-%m-%d')}...")

                    max_output_tokens = (
                        800 if content_length.lower() == "short"
                        else 2000 if content_length.lower() == "medium"
                        else 3000
                    )

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

                        # Clean up unnecessary lines
                        for bad_phrase in [
                            "Here's a possible LinkedIn post, ready to copy and paste:",
                            "HASHTAGS:",
                            "Hashtags:",
                            "**HASHTAGS:**"
                        ]:
                            text_output = text_output.replace(bad_phrase, "")
                        text_output = text_output.strip()

                        html_output = markdown.markdown(text_output, extensions=['extra', 'smarty'])
                        print(f"[DEBUG] ✅ Generated for {scheduled_date.strftime('%Y-%m-%d')} ({len(text_output)} chars)")

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

                session['daywise_content'] = daywise_content
                flash("✅ Content generated successfully! Review your posts below.", "success")
                return render_template('daywise_preview.html', daywise_content=daywise_content)

            except Exception as e:
                import traceback
                print(traceback.format_exc())
                flash(f"❌ Error generating content: {str(e)}", "danger")

    if 'daywise_content' in session:
        return render_template('daywise_preview.html', daywise_content=session['daywise_content'])

    return render_template('text_generation.html')

# -------------------------------
# ROUTE: Add a new scheduled post
# -------------------------------
@app.route('/add_post', methods=['GET', 'POST'])
def add_post():
    if request.method == 'POST':
        post_date = request.form.get('post_date')
        content = request.form.get('content')
        added_by = "AI Generator"

        if not post_date or not content:
            flash("⚠️ Please fill in all required fields.", "warning")
            return redirect(url_for('add_post'))

        try:
            cur = mysql.connection.cursor()
            cur.execute("""
                INSERT INTO scheduled_posts (post_date, content, added_by)
                VALUES (%s, %s, %s)
            """, (post_date, content, added_by))
            mysql.connection.commit()
            cur.close()
            flash("✅ Post added successfully!", "success")
            return redirect(url_for('view_posts'))
        except Exception as e:
            flash(f"❌ Database Error: {str(e)}", "danger")

    return render_template('add_post.html')

# -------------------------------
# ROUTE: Save generated day-wise schedule to DB
# -------------------------------
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
                cursor = mysql.connection.cursor()
                cursor.execute("""
                    INSERT INTO scheduled_posts 
                    (post_date, content, added_by, author_urn, posted, added_date, updated_by, updated_date)
                    VALUES (%s, %s, %s, %s, %s, NOW(), NULL, NULL)
                """, (post_date, post_content.strip(), added_by, author_urn, 0))
                mysql.connection.commit()
                cursor.close()
                saved_count += 1
                print(f"[DEBUG] Saved post with author_urn={author_urn}")
            except Exception as e:
                print(f"[ERROR] Failed to save post: {e}")
                flash(f"❌ Error saving post: {str(e)}", "danger")
                continue

    flash(f"✅ {saved_count} post(s) saved successfully!", "success")
    return redirect(url_for('view_posts'))


# -------------------------------
# ROUTE: View all scheduled posts
# -------------------------------
@app.route("/view_posts")
def view_posts():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM scheduled_posts")
    posts = cur.fetchall()

    # Group posts by date
    posts_by_date = {}
    for post in posts:
        posts_by_date.setdefault(post["post_date"], []).append(post)

    return render_template("view_posts.html", posts_by_date=posts_by_date)

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
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
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

@app.route('/clear_and_generate')
def clear_and_generate():
    # Clear the daywise_content from session
    if 'daywise_content' in session:
        session.pop('daywise_content')
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