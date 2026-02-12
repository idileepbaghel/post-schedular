from flask import Blueprint, jsonify, current_app
from extensions import mysql
from datetime import datetime
import requests
import os
import traceback

scheduler_bp = Blueprint('scheduler_bp', __name__)

@scheduler_bp.route('/run_scheduled_posts')
def run_scheduled_posts():
    """Background job to post scheduled content (text + up to 5 images) to LinkedIn"""
    print(f"[{datetime.now()}] Checking for posts to publish...")

    try:
        # Use current_app.app_context() instead of app.app_context()
        with current_app.app_context():
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
                
                # Multiple image support (CSV format)
                image_field = post.get("image")
                image_list = [
                    img.strip() for img in image_field.split(",")
                    if img.strip()
                ] if image_field else []

                # Limit max 5
                image_list = image_list[:5]

                print(f"\nPreparing Post ID={post_id} with {len(image_list)} image(s)")

                # Fetch LinkedIn Access Token
                cursor.execute("SELECT access_token FROM linkedin_tokens WHERE user_urn = %s", (author_urn,))
                token_row = cursor.fetchone()

                if not token_row:
                    print(f"No access token found for author_urn={author_urn}")
                    failed_posts.append({
                        "post_id": post_id,
                        "reason": "No access token found"
                    })
                    continue

                access_token = token_row["access_token"]

                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0"
                }

                # === STEP 1: Upload multiple images to LinkedIn ===
                asset_list = []

                for img_name in image_list:
                    # Fix path reference using current_app.root_path
                    image_path = os.path.join(current_app.root_path, "static", "uploaded_post_img", img_name)

                    if not os.path.exists(image_path):
                        print(f"[SKIP] Image not found: {image_path}")
                        continue

                    print(f"[UPLOAD] Registering upload for {img_name}")

                    register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
                    register_body = {
                        "registerUploadRequest": {
                            "owner": f"urn:li:person:{author_urn}",
                            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                            "serviceRelationships": [
                                {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
                            ]
                        }
                    }

                    reg_response = requests.post(
                        register_url,
                        headers={**headers, "Content-Type": "application/json"},
                        json=register_body
                    )

                    if reg_response.status_code not in [200, 201]:
                        print(f"[REGISTER ERROR] {reg_response.text}")
                        continue

                    reg_json = reg_response.json()
                    upload_url = reg_json["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
                    asset_urn = reg_json["value"]["asset"]

                    print(f"[UPLOAD] Got upload URL for asset {asset_urn}")

                    # Upload actual image bytes
                    with open(image_path, "rb") as img_file:
                        upload_resp = requests.put(
                            upload_url,
                            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "image/jpeg"},
                            data=img_file
                        )

                    if upload_resp.status_code in [200, 201]:
                        print(f"[UPLOAD] Successfully uploaded {img_name}")
                        asset_list.append(asset_urn)
                    else:
                        print(f"[UPLOAD ERROR] {upload_resp.text}")

                # === STEP 2: Prepare LinkedIn Post Body ===
                media_category = "IMAGE" if asset_list else "NONE"

                data = {
                    "author": f"urn:li:person:{author_urn}",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": content},
                            "shareMediaCategory": media_category
                        }
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                    }
                }

                # Add all images into media array 
                if asset_list:
                    data["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
                        {"status": "READY", "media": asset}
                        for asset in asset_list
                    ]

                # === STEP 3: Publish post ===
                post_response = requests.post(
                    "https://api.linkedin.com/v2/ugcPosts",
                    headers={**headers, "Content-Type": "application/json"},
                    json=data
                )

                if post_response.status_code in [200, 201]:
                    print(f"[SUCCESS] Successfully posted ID={post_id}")

                    cursor.execute("""
                        UPDATE scheduled_posts 
                        SET posted = 1,
                            posted_at = NOW(),
                            updated_date = NOW(),
                            updated_by = 'System'
                        WHERE id = %s
                    """, (post_id,))
                    mysql.connection.commit()

                    successful_posts.append({"post_id": post_id, "author_urn": author_urn})

                else:
                    print(f"[POST ERROR] {post_response.status_code} - {post_response.text}")
                    failed_posts.append({
                        "post_id": post_id,
                        "status_code": post_response.status_code,
                        "error": post_response.text
                    })

            cursor.close()

            print("\nCompleted scheduled post check.\n")

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
        print(f"\n[EXCEPTION] {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Error during scheduled posting."
        }), 500