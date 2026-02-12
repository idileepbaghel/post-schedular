from flask import Blueprint, render_template, request, flash, redirect, url_for, session, current_app
from extensions import mysql
from decorators import login_required
import os
import json
import requests
import time
from werkzeug.utils import secure_filename

post_bp = Blueprint('post_bp', __name__)

# =========================================================
# 1. VIEW POSTS ROUTE
# =========================================================
@post_bp.route("/view_posts")
@login_required
def view_posts():
    auth_urn = session.get('linkedin_user_urn')
    user_pic = session.get('user_pic')

    cur = mysql.connection.cursor()
    # Ensure your DB table has 'topic_context' column
    cur.execute("SELECT * FROM scheduled_posts WHERE author_urn = %s ORDER BY id DESC", (auth_urn,))
    posts = cur.fetchall()
    cur.close()

    all_posts = []
    for post in posts:
        post_date = post.get("scheduled_time") or post.get("post_date")
        post["display_date"] = post_date

        image_data = post.get("image")
        image_urls = []

        if image_data:
            try:
                # Try parsing as JSON list first
                if image_data.strip().startswith('['):
                    img_list = json.loads(image_data)
                else:
                    # Fallback: comma-separated string
                    img_list = [img.strip() for img in image_data.split(',') if img.strip()]
                
                # Build correct URLs
                image_urls = [
                    url_for('static', filename=f"uploaded_post_img/{img_name}")
                    for img_name in img_list
                ]
            except Exception as e:
                print(f"[ERROR] Image parsing failed for post {post.get('id')}: {e}")
                image_urls = []

        post["image_urls"] = image_urls
        all_posts.append(post)

    return render_template("view_posts.html", all_posts=all_posts, user_pic=user_pic)


# =========================================================
# 2. UPDATE POST ROUTE
# =========================================================
@post_bp.route('/update_post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def update_post(post_id):
    author_urn = session.get('linkedin_user_urn')
    cursor = mysql.connection.cursor()

    # --- GET REQUEST: Show the Edit Form ---
    if request.method == 'GET':
        cursor.execute("SELECT * FROM scheduled_posts WHERE id=%s AND author_urn=%s", (post_id, author_urn))
        post = cursor.fetchone()
        cursor.close()
        
        if not post:
            flash("❌ Post not found or permission denied.", "danger")
            return redirect(url_for('post_bp.view_posts'))
            
        return render_template('update_post.html', post=post)

    # --- POST REQUEST: Process the Update ---
    if request.method == 'POST':
        new_date = request.form.get('post_date')
        new_content = request.form.get('content')
        updated_by = session.get('linkedin_user', 'User')
        
        new_image = request.files.get('image')

        try:
            # 1. Prepare SQL
            sql = "UPDATE scheduled_posts SET post_date=%s, content=%s, updated_by=%s, updated_date=NOW()"
            params = [new_date, new_content, updated_by]

            # 2. Handle Image Update
            if new_image and new_image.filename:
                # Optional: Delete old image
                cursor.execute("SELECT image FROM scheduled_posts WHERE id=%s", (post_id,))
                old_post = cursor.fetchone()
                
                # Clean up old file if exists
                if old_post and old_post['image']:
                    old_path = os.path.join(current_app.root_path, 'static', 'uploaded_post_img', old_post['image'])
                    if os.path.exists(old_path):
                        os.remove(old_path)

                # Save New Image
                filename = secure_filename(new_image.filename)
                new_filename = f"updated_{int(time.time())}_{filename}"
                save_path = os.path.join(current_app.root_path, 'static', 'uploaded_post_img', new_filename)
                new_image.save(save_path)
                
                sql += ", image=%s"
                params.append(new_filename)

            # 3. Add WHERE clause
            sql += " WHERE id=%s AND author_urn=%s"
            params.append(post_id)
            params.append(author_urn)

            # 4. Execute
            cursor.execute(sql, tuple(params))
            mysql.connection.commit()
            cursor.close()
            
            flash("✅ Post updated successfully!", "success")
            return redirect(url_for('post_bp.view_posts'))

        except Exception as e:
            print(f"Update Error: {e}")
            flash("❌ Error updating post.", "danger")
            return redirect(url_for('post_bp.view_posts'))


# =========================================================
# 3. DELETE POST ROUTE
# =========================================================
@post_bp.route('/delete_post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def delete_post(post_id):
    author_urn = session.get('linkedin_user_urn')
    cursor = mysql.connection.cursor()
    
    # 1. Verify ownership
    cursor.execute("SELECT image FROM scheduled_posts WHERE id=%s AND author_urn=%s", (post_id, author_urn))
    post = cursor.fetchone()
    
    if not post:
        flash("❌ Post not found or permission denied.", "danger")
        return redirect(url_for('post_bp.view_posts'))
    
    # 2. Delete physical files
    if post['image']:
        try:
            images = post['image'].split(',')
            for img in images:
                file_path = os.path.join(current_app.root_path, 'static', 'uploaded_post_img', img.strip())
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"[DEBUG] Deleted file: {file_path}")
        except Exception as e:
            print(f"[ERROR] Could not delete image file: {e}")

    # 3. Delete from DB
    cursor.execute("DELETE FROM scheduled_posts WHERE id=%s", (post_id,))
    mysql.connection.commit()
    cursor.close()
    
    flash("🗑️ Post deleted successfully.", "success")
    return redirect(url_for('post_bp.view_posts'))

# ... (keep existing imports)

# =========================================================
# 6. BULK DELETE ROUTE (NEW)
# =========================================================
@post_bp.route('/bulk_delete_posts', methods=['POST'])
@login_required
def bulk_delete_posts():
    author_urn = session.get('linkedin_user_urn')
    
    # Get list of IDs from the form. 
    # Frontend must send input with name="post_ids" (multiple)
    post_ids = request.form.getlist('post_ids') 
    
    if not post_ids:
        flash("⚠️ No posts selected for deletion.", "warning")
        return redirect(url_for('post_bp.view_posts'))

    # Convert to integers and validate
    try:
        clean_ids = [int(pid) for pid in post_ids]
    except ValueError:
        flash("❌ Invalid post IDs.", "danger")
        return redirect(url_for('post_bp.view_posts'))

    if not clean_ids:
        return redirect(url_for('post_bp.view_posts'))

    cursor = mysql.connection.cursor()
    
    # 1. Fetch images for cleanup BEFORE deleting records
    # We create a placeholder string like "%s, %s, %s" based on number of IDs
    format_strings = ','.join(['%s'] * len(clean_ids))
    
    # Combine user URN + ID list for params
    query_params = [author_urn] + clean_ids
    
    try:
        # Security: Verify ownership in the SELECT
        cursor.execute(f"SELECT image FROM scheduled_posts WHERE author_urn=%s AND id IN ({format_strings})", query_params)
        posts_to_delete = cursor.fetchall()

        # Delete physical files
        for post in posts_to_delete:
            if post['image']:
                try:
                    # Handle multiple images (CSV) or single
                    images = post['image'].split(',') if ',' in post['image'] else [post['image']]
                    for img in images:
                        file_path = os.path.join(current_app.root_path, 'static', 'uploaded_post_img', img.strip())
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            print(f"[DEBUG] Bulk deleted file: {file_path}")
                except Exception as e:
                    print(f"[ERROR] Bulk file delete error: {e}")

        # 2. Delete records from DB
        # Security: Ensure we only delete rows matching IDs AND the current Author URN
        delete_query = f"DELETE FROM scheduled_posts WHERE author_urn=%s AND id IN ({format_strings})"
        cursor.execute(delete_query, query_params)
        mysql.connection.commit()
        
        deleted_count = cursor.rowcount
        flash(f"✅ Successfully deleted {deleted_count} posts.", "success")

    except Exception as e:
        print(f"[ERROR] Bulk delete failed: {e}")
        flash("❌ Error deleting posts.", "danger")
    finally:
        cursor.close()
    
    return redirect(url_for('post_bp.view_posts'))

# =========================================================
# 4. ADD POST MANUALLY ROUTE
# =========================================================
@post_bp.route('/add_post', methods=['GET', 'POST'])
@login_required
def add_post():
    if request.method == 'POST':
        post_date = request.form.get('post_date')
        content = request.form.get('content')
        added_by = session.get('linkedin_user', 'Unknown User')

        if not post_date or not content:
            flash("⚠️ Please fill in all required fields.", "warning")
            return redirect(url_for('post_bp.add_post'))

        try:
            cur = mysql.connection.cursor()
            cur.execute("""
                INSERT INTO scheduled_posts (post_date, content, added_by, author_urn, added_date)
                VALUES (%s, %s, %s, %s, NOW())
            """, (post_date, content, added_by, session.get('linkedin_user_urn')))
            mysql.connection.commit()
            cur.close()
            flash("✅ Post added successfully!", "success")
            return redirect(url_for('post_bp.view_posts'))
        except Exception as e:
            print(f"[ERROR] Failed to add post: {e}")
            flash(f"❌ Database Error: {str(e)}", "danger")

    return render_template('add_post.html')


# =========================================================
# 5. POST TO LINKEDIN ROUTE (The complex one)
# =========================================================
@post_bp.route('/post_to_linkedin', methods=['POST'])
@login_required
def post_to_linkedin():
    print("\n=== [DEBUG] /post_to_linkedin (Multi-Image) ROUTE CALLED ===")
    
    access_token = session.get("linkedin_token")
    if not access_token:
        flash("⚠️ Please connect to LinkedIn first.", "warning")
        return redirect(url_for('post_bp.view_posts'))
    
    post_id = request.form.get('post_id')
    content = request.form.get('content', '').strip()
    
    if not post_id:
        flash("❌ Error: Missing Post ID.", "danger")
        return redirect(url_for('post_bp.view_posts'))

    cursor = mysql.connection.cursor()
    
    # 1. Fetch Post Details
    cursor.execute("SELECT image, author_urn FROM scheduled_posts WHERE id=%s", (post_id,))
    post_data = cursor.fetchone()
    
    if not post_data:
        flash("❌ Post not found in database.", "danger")
        return redirect(url_for('post_bp.view_posts'))

    image_data_str = post_data.get('image')
    author_urn = post_data.get('author_urn')
    person_urn = f"urn:li:person:{author_urn}"

    uploaded_asset_urns = []
    
    # === STEP 1: LOOP & UPLOAD ALL IMAGES ===
    if image_data_str:
        image_list = [img.strip() for img in image_data_str.split(',') if img.strip()]
        print(f"[DEBUG] Found {len(image_list)} images to upload: {image_list}")

        for img_name in image_list:
            image_path = os.path.join(current_app.root_path, 'static', 'uploaded_post_img', img_name)
            
            if os.path.exists(image_path):
                print(f"[DEBUG] Processing image: {img_name}")
                
                # A. Register Upload
                register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
                register_json = {
                    "registerUploadRequest": {
                        "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                        "owner": person_urn,
                        "serviceRelationships": [{
                            "relationshipType": "OWNER",
                            "identifier": "urn:li:userGeneratedContent"
                        }]
                    }
                }
                
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                reg_resp = requests.post(register_url, headers=headers, json=register_json)
                
                if reg_resp.status_code == 200:
                    upload_data = reg_resp.json()
                    upload_url = upload_data['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
                    asset_urn = upload_data['value']['asset']
                    
                    # B. Upload Binary Data
                    try:
                        with open(image_path, 'rb') as img_file:
                            upload_resp = requests.put(
                                upload_url, 
                                headers={"Authorization": f"Bearer {access_token}"}, 
                                data=img_file
                            )
                        
                        if upload_resp.status_code == 201:
                            print(f"[SUCCESS] Uploaded {img_name}")
                            uploaded_asset_urns.append(asset_urn)
                        else:
                            print(f"[ERROR] Binary upload failed for {img_name}: {upload_resp.text}")
                    except Exception as e:
                        print(f"[ERROR] File error for {img_name}: {e}")
                else:
                    print(f"[ERROR] Register failed for {img_name}: {reg_resp.text}")
            else:
                print(f"[WARNING] File not found on server: {image_path}")

    # === STEP 2: CREATE POST ===
    post_url = "https://api.linkedin.com/v2/ugcPosts"
    
    post_body = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": content},
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    }

    if uploaded_asset_urns:
        post_body["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "IMAGE"
        media_array = []
        for asset in uploaded_asset_urns:
            media_array.append({
                "status": "READY",
                "description": {"text": "Image posted via LearnTrail AI"},
                "media": asset,
                "title": {"text": "LearnTrail Post"}
            })
        post_body["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = media_array

    headers = {
        "Authorization": f"Bearer {access_token}", 
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    
    final_resp = requests.post(post_url, headers=headers, json=post_body)
    
    if final_resp.status_code in [200, 201]:
        # === STEP 3: UPDATE DB ===
        cursor.execute("UPDATE scheduled_posts SET posted=1, posted_at=NOW() WHERE id=%s", (post_id,))
        mysql.connection.commit()
        flash("✅ Successfully posted to LinkedIn!", "success")
    else:
        print(f"[ERROR] LinkedIn Post Failed: {final_resp.text}")
        flash(f"❌ Failed to post: {final_resp.json().get('message', 'Unknown error')}", "danger")

    cursor.close()
    return redirect(url_for('post_bp.view_posts'))