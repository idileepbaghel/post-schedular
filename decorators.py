from functools import wraps
from flask import session, flash, redirect, url_for
from extensions import mysql

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for('auth_bp.signin'))

        cur = mysql.connection.cursor()
        cur.execute("SELECT user_urn FROM linkedin_tokens WHERE id=%s", (session['user_id'],))
        user = cur.fetchone()
        cur.close()

        if not user or not user['user_urn']:
            flash("⚠️ Please verify your LinkedIn account to continue.", "warning")
            return redirect(url_for('auth_bp.verify_social'))

        return f(*args, **kwargs)
    return decorated_function