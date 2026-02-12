from flask import Blueprint, render_template, request, flash, redirect, url_for, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import mysql
from decorators import login_required
from datetime import datetime
import os
import secrets
import urllib.parse
import requests
import traceback

auth_bp = Blueprint('auth_bp', __name__)

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
# Ensure this matches your production URL when deploying
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", 'http://localhost:5500/linkedin/callback')

# ================================
# SIGNIN ROUTES
# ================================
@auth_bp.route('/signin')
def signin():
    """Sign in page - redirects to main page if already logged in"""
    if 'user_id' in session and 'linkedin_token' in session:
        return redirect(url_for('content_bp.generate_text'))
    return render_template('auth.html', page='signin')


@auth_bp.route('/signin_post', methods=['POST'])
def signin_post():
    email = request.form['email']
    password = request.form['password']

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM linkedin_tokens WHERE user_email=%s", (email,))
    user = cur.fetchone()
    cur.close()

    if not user or not check_password_hash(user['password'], password):
        flash("❌ Invalid email or password.", "danger")
        return redirect(url_for('auth_bp.signin'))

    # Save session
    session['user_id'] = user['id']
    session['user_email'] = user['user_email']
    session['linkedin_user'] = user['user_name']
    session['linkedin_user_urn'] = user.get('user_urn')
    session['linkedin_token'] = user.get('access_token')
    
    # Check LinkedIn verification
    if not user.get('user_urn') or not user.get('access_token'):
        flash("⚠️ Please verify your LinkedIn account before using the panel.", "warning")
        return redirect(url_for('auth_bp.verify_social'))

    flash(f"✅ Welcome back, {user['user_name']}!", "success")
    return redirect(url_for('content_bp.generate_text'))


# ================================
# SIGNUP ROUTES
# ================================
@auth_bp.route('/signup')
def signup():
    if 'user_id' in session and 'linkedin_token' in session:
        return redirect(url_for('content_bp.generate_text'))
    return render_template('auth.html', page='signup')


@auth_bp.route('/signup_post', methods=['POST'])
def signup_post():
    name = request.form['name']
    email = request.form['email']
    password = request.form['password']
    password_hash = generate_password_hash(password)

    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM linkedin_tokens WHERE user_email=%s", (email,))
    existing = cur.fetchone()

    if existing:
        flash("⚠️ Email already registered. Please sign in.", "warning")
        return redirect(url_for('auth_bp.signin'))

    cur.execute("""
        INSERT INTO linkedin_tokens (user_name, user_email, password, added_by, added_date, updated_by, updated_date)
        VALUES (%s, %s, %s, 'System', NOW(), 'System', NOW())
    """, (name, email, password_hash))
    mysql.connection.commit()
    user_id = cur.lastrowid
    cur.close()

    session['user_id'] = user_id
    session['user_email'] = email
    session['linkedin_user'] = name

    flash("✅ Account created! Please verify your LinkedIn account to access services.", "info")
    return redirect(url_for('auth_bp.verify_social'))


# ================================
# LOGOUT & VERIFY ROUTES
# ================================
@auth_bp.route('/logout')
def logout():
    session.clear()
    flash("✅ You have been logged out successfully.", "success")
    return redirect(url_for('auth_bp.signin'))


@auth_bp.route('/verify_social')
def verify_social():
    if 'user_id' not in session:
        flash("Please sign in first.", "warning")
        return redirect(url_for('auth_bp.signin'))

    cur = mysql.connection.cursor()
    cur.execute("SELECT user_urn FROM linkedin_tokens WHERE id=%s", (session['user_id'],))
    user = cur.fetchone()
    cur.close()

    if user and user['user_urn']:
        return redirect(url_for('content_bp.generate_text'))

    return render_template('verify_social.html')


# ================================
# LINKEDIN OAUTH ROUTES
# ================================
@auth_bp.route('/linkedin/login')
def linkedin_login():
    if not LINKEDIN_CLIENT_ID:
        flash("⚠️ LinkedIn Client ID not configured.", "danger")
        return redirect(url_for('auth_bp.signin'))

    scopes = "openid profile email w_member_social"
    encoded_redirect = urllib.parse.quote(LINKEDIN_REDIRECT_URI, safe='')
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
    return redirect(auth_url)


@auth_bp.route('/linkedin/callback')
def linkedin_callback():
    error = request.args.get("error")
    error_description = request.args.get("error_description")

    if error:
        flash(f"LinkedIn login error: {error} - {error_description}", "danger")
        return redirect(url_for('auth_bp.signin'))

    code = request.args.get("code")
    if not code:
        flash("LinkedIn authorization failed: no code provided.", "danger")
        return redirect(url_for('auth_bp.signin'))

    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET
    }

    try:
        r = requests.post(token_url, data=data, timeout=10)
        r.raise_for_status()
        token_data = r.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            flash("⚠️ Failed to retrieve access token from LinkedIn.", "danger")
            return redirect(url_for('auth_bp.signin'))

        # Get Profile Info
        profile_url = "https://api.linkedin.com/v2/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        profile_response = requests.get(profile_url, headers=headers, timeout=10)

        if profile_response.status_code != 200:
            flash("❌ Failed to fetch LinkedIn profile.", "danger")
            return redirect(url_for('auth_bp.signin'))

        profile_data = profile_response.json()
        user_sub = profile_data.get("sub")
        user_name = profile_data.get("name", "LinkedIn User")
        user_email = profile_data.get("email", "")
        user_pic = profile_data.get("picture", "")

        session['user_pic'] = user_pic

        if not user_sub:
            flash("⚠️ Could not retrieve LinkedIn user ID.", "danger")
            return redirect(url_for('auth_bp.signin'))

        cur = mysql.connection.cursor()

        # Case 1: Existing logged-in user linking LinkedIn
        if 'user_id' in session:
            cur.execute("""
                UPDATE linkedin_tokens
                SET user_urn=%s, access_token=%s, user_name=%s, user_email=%s, updated_by='System', updated_date=NOW()
                WHERE id=%s
            """, (user_sub, access_token, user_name, user_email, session['user_id']))
            mysql.connection.commit()
            
            flash("✅ LinkedIn verified successfully!", "success")

        # Case 2: Logging in directly via LinkedIn
        else:
            cur.execute("SELECT * FROM linkedin_tokens WHERE user_urn=%s", (user_sub,))
            existing_user = cur.fetchone()

            if existing_user:
                cur.execute("""
                    UPDATE linkedin_tokens
                    SET access_token=%s, user_name=%s, user_email=%s, updated_by='System', updated_date=NOW()
                    WHERE user_urn=%s
                """, (access_token, user_name, user_email, user_sub))
                mysql.connection.commit()
                user_id = existing_user['id']
                flash(f"✅ Welcome back, {user_name}!", "success")
            else:
                cur.execute("""
                    INSERT INTO linkedin_tokens
                    (user_urn, access_token, user_name, user_email, added_by, added_date, updated_by, updated_date)
                    VALUES (%s, %s, %s, %s, 'System', NOW(), 'System', NOW())
                """, (user_sub, access_token, user_name, user_email))
                mysql.connection.commit()
                user_id = cur.lastrowid
                flash(f"✅ Welcome {user_name}! Account created via LinkedIn.", "success")

            session['user_id'] = user_id

        cur.close()

        # Final Session Setup
        session['linkedin_token'] = access_token
        session['linkedin_user'] = user_name
        session['linkedin_user_urn'] = user_sub
        session['user_email'] = user_email

        return redirect(url_for('content_bp.generate_text'))

    except Exception as e:
        print(f"[ERROR] LinkedIn Login: {e}")
        print(traceback.format_exc())
        flash(f"❌ Error during LinkedIn login: {str(e)}", "danger")
        return redirect(url_for('auth_bp.signin'))


# ================================
# PROFILE ROUTES
# ================================
@auth_bp.route('/profile')
@login_required
def profile():
    """Display user profile with all session data"""
    
    profile_data = {
        'user_id': session.get('user_id'),
        'user_name': session.get('linkedin_user', 'N/A'),
        'user_email': session.get('user_email', 'N/A'),
        'user_pic': session.get('user_pic', 'https://cdn-icons-png.flaticon.com/512/847/847969.png'),
        'linkedin_user_urn': session.get('linkedin_user_urn'),
        'has_linkedin': bool(session.get('linkedin_token') and session.get('linkedin_user_urn')),
        'linkedin_verified': bool(session.get('linkedin_user_urn'))
    }
    
    cur = mysql.connection.cursor()
    
    # Statistics
    stats_query = """
        SELECT 
            (SELECT COUNT(*) FROM scheduled_posts WHERE author_urn = %s) as total,
            (SELECT COUNT(*) FROM scheduled_posts WHERE author_urn = %s AND posted = 0) as scheduled,
            (SELECT COUNT(*) FROM scheduled_posts WHERE author_urn = %s AND posted = 1) as published
    """
    urn = profile_data['linkedin_user_urn']
    cur.execute(stats_query, (urn, urn, urn))
    stats = cur.fetchone()
    
    profile_data['total_posts'] = stats['total'] if stats else 0
    profile_data['scheduled_posts'] = stats['scheduled'] if stats else 0
    profile_data['published_posts'] = stats['published'] if stats else 0
    
    # Account Created Date
    cur.execute("SELECT added_date FROM linkedin_tokens WHERE id = %s", (profile_data['user_id'],))
    account_info = cur.fetchone()
    profile_data['account_created'] = account_info['added_date'] if account_info else None
    
    cur.close()
    
    return render_template('profile.html', profile=profile_data)


@auth_bp.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    """Update user profile information"""
    
    user_name = request.form.get('user_name', '').strip()
    user_email = request.form.get('user_email', '').strip()
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    
    if not user_name or not user_email:
        flash("❌ Name and email are required.", "danger")
        return redirect(url_for('auth_bp.profile'))
    
    cur = mysql.connection.cursor()
    
    if new_password:
        if not current_password:
            flash("❌ Current password is required to set a new password.", "danger")
            return redirect(url_for('auth_bp.profile'))
        
        cur.execute("SELECT password FROM linkedin_tokens WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        
        if not user or not check_password_hash(user['password'], current_password):
            flash("❌ Current password is incorrect.", "danger")
            cur.close()
            return redirect(url_for('auth_bp.profile'))
        
        hashed_password = generate_password_hash(new_password)
        cur.execute("""
            UPDATE linkedin_tokens 
            SET user_name = %s, user_email = %s, password = %s, updated_by = 'User', updated_date = NOW()
            WHERE id = %s
        """, (user_name, user_email, hashed_password, session['user_id']))
        flash("✅ Profile and password updated successfully!", "success")
    else:
        cur.execute("""
            UPDATE linkedin_tokens 
            SET user_name = %s, user_email = %s, updated_by = 'User', updated_date = NOW()
            WHERE id = %s
        """, (user_name, user_email, session['user_id']))
        flash("✅ Profile updated successfully!", "success")
    
    mysql.connection.commit()
    cur.close()
    
    session['linkedin_user'] = user_name
    session['user_email'] = user_email
    
    return redirect(url_for('auth_bp.profile'))