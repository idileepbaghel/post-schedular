from flask import Blueprint, render_template, request, flash, redirect, url_for, session, current_app
from extensions import mysql, client
from decorators import login_required
from google.genai import types
import markdown
from datetime import datetime, timedelta
import os
import io
import base64
import time
import shutil
import random as py_random
from PIL import Image
from werkzeug.utils import secure_filename

content_bp = Blueprint('content_bp', __name__)

# =========================================================
# 1. GENERATE TEXT ROUTE (Saves topic to session)
# =========================================================
@content_bp.route('/', methods=['GET', 'POST'])
@login_required
def generate_text():
    """Main content generation page"""
    
    if request.method == 'POST':
        if not session.get('linkedin_token'):
            flash("🔗 Please connect to LinkedIn first before generating content.", "warning")
            return redirect(url_for('auth_bp.linkedin_login'))

        # Extract Form Data
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

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            daywise_content = []

            schedule_map = {
                "Single Day": 1, "2 Days": 2, "5 Days": 5,
                "10 Days": 10, "1 Week": 7, "2 Weeks": 14
            }
            num_days = schedule_map.get(content_schedule, 1)
            schedule_items = [start_date + timedelta(days=i) for i in range(num_days)]

            error_flag = False 

            for scheduled_date in schedule_items:
                # --- PROMPT CONSTRUCTION ---
                hashtags_instruction = hashtags.strip() if hashtags else ''
                
                # UPDATED LENGTH INSTRUCTIONS: Enforcing minimum ~500 chars
                length_instruction = ""
                
                # We increase token limits to ensure the API has enough "space" to write the minimum characters
                # 1 token ~= 4 chars usually, so 1000 tokens is safe for >500 chars
                if content_length == "Short":
                    length_instruction = "Strict Requirement: Write at least 500 characters. Keep it concise but ensure it meets this minimum length."
                    max_output_tokens = 1000 
                elif content_length == "Medium":
                    length_instruction = "Standard LinkedIn length (approx 1000-1500 characters). Minimum 500 characters required."
                    max_output_tokens = 2000
                elif content_length == "Lengthy":
                    length_instruction = "In-depth thought leadership (approx 2000+ characters). Detailed storytelling or analysis."
                    max_output_tokens = 4000
                else:
                    # Fallback
                    length_instruction = "Write at least 500 characters."
                    max_output_tokens = 2000

                system_message = "You are a professional LinkedIn content writer. Your goal is to write engaging, human-sounding content."
                
                prompt_body = f"\n\nSCHEDULED DATE: {scheduled_date.strftime('%A, %B %d, %Y')}\n"
                prompt_body += f"Topic / Context: {topic_context}\n"
                
                # Explicitly passing the length constraint
                prompt_body += f"Desired Length: {content_length} - {length_instruction}\n"
                
                prompt_body += f"Purpose: {purpose_goal}\n"
                prompt_body += f"Target Audience: {target_audience}\n"
                prompt_body += f"Tone of Voice: {tone_of_voice}\n"
                prompt_body += f"Format Style: {formatting}\n"
                
                if keywords: prompt_body += f"Keywords to include: {keywords}\n"
                if cta: prompt_body += f"Call to Action (CTA): {cta}\n"
                
                if hashtags_instruction: prompt_body += f"Hashtags: {hashtags_instruction}\n"
                else: prompt_body += "Hashtags: Generate 4-6 relevant and trending hashtags.\n"
                
                if user_prompt: prompt_body += f"Additional Instructions: {user_prompt}\n"
                
                # Instruction to ensure it doesn't just give a summary
                prompt_body += "\nIMPORTANT: Do not output a summary. Output the actual post text ready to be published."

                try:
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f"{system_message}{prompt_body}",
                        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens, temperature=0.7)
                    )

                    if response and response.text:
                        text_output = response.text.strip()
                        # Cleanup specific phrases...
                        for bad in ["Here's a possible LinkedIn post", "**HASHTAGS:**", "Here is a post:", "## LinkedIn Post"]:
                            text_output = text_output.replace(bad, "")
                        
                        html_output = markdown.markdown(text_output.strip())

                        daywise_content.append({
                            "date": scheduled_date.strftime("%Y-%m-%d"),
                            "text": text_output.strip(),
                            "html": html_output,
                            "topic_context": topic_context
                        })
                    else:
                        if not error_flag: 
                            flash("Error generating content: AI returned empty response.", "danger")
                            error_flag = True
                            
                        daywise_content.append({
                            "date": scheduled_date.strftime("%Y-%m-%d"),
                            "text": "(No content generated)",
                            "html": "(No content generated)",
                            "topic_context": topic_context
                        })

                except Exception as gen_err:
                    print(f"Gen Error: {gen_err}")
                    if not error_flag:
                        flash("Error generating content. Please check your API key or try again.", "danger")
                        error_flag = True

                    daywise_content.append({
                        "date": scheduled_date.strftime("%Y-%m-%d"),
                        "text": "Error generating content",
                        "html": "Error",
                        "topic_context": topic_context
                    })

            session['daywise_content'] = daywise_content
            
            if not error_flag:
                flash("Content generated successfully!", "success")
            
            return render_template('daywise_preview.html', daywise_content=daywise_content)

        except Exception as e:
            print(f"Error: {e}")
            flash("System Error: Could not process request.", "danger")
            return render_template('text_generation.html')

    if 'daywise_content' in session:
        return render_template('daywise_preview.html', daywise_content=session['daywise_content'])

    return render_template('text_generation.html')


# =========================================================
# 2. GENERATE IMAGE ROUTE
# =========================================================
@content_bp.route('/generate_image', methods=['POST'])
@login_required
def generate_image():
    post_text = request.form.get('post_text', "").strip()
    post_index = request.form.get('post_index', "").strip()

    if not post_text:
        flash("⚠️ No text found to generate an image.", "warning")
        return redirect(url_for('content_bp.generate_text'))

    try:
        # STEP 1: Extract visual concept
        extraction_prompt = f"Extract core visual theme in 5 words for image generation:\n{post_text[:300]}"
        
        concept_response = client.models.generate_content(
            model="gemini-2.5-flash", contents=extraction_prompt,
            config=types.GenerateContentConfig(max_output_tokens=30)
        )
        image_concept = concept_response.text.strip() if concept_response and concept_response.text else "professional business scene"

        # STEP 2: Generate Image
        image_prompt = f"Professional image: {image_concept}. Modern, minimal, business aesthetic."
        generated_image_b64 = None
        
        # Simple retry logic
        for _ in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash", contents=image_prompt,
                    config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"], candidate_count=1)
                )
                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "inline_data") and part.inline_data:
                            img = Image.open(io.BytesIO(part.inline_data.data))
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            generated_image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                            break
                if generated_image_b64: break
            except Exception:
                time.sleep(1)

        if not generated_image_b64:
            flash("⚠️ Could not generate image.", "warning")
            return redirect(url_for('content_bp.generate_text'))

        # STEP 3: Save Image
        # Use current_app.root_path to get correct static path
        save_dir = os.path.join(current_app.root_path, "static", "generated_image")
        os.makedirs(save_dir, exist_ok=True)

        filename = f"generated_{int(time.time())}_{post_index}.png"
        filepath = os.path.join(save_dir, filename)

        with open(filepath, "wb") as f:
            f.write(base64.b64decode(generated_image_b64))

        # STEP 4: Update Session
        daywise_content = session.get('daywise_content', [])
        idx = int(post_index) - 1
        if 0 <= idx < len(daywise_content):
            daywise_content[idx]["image"] = filename
            session['daywise_content'] = daywise_content
            session.modified = True
            flash("🎨 Image generated!", "success")
        
        return redirect(url_for('content_bp.generate_text'))

    except Exception as e:
        print(f"[ERROR] {e}")
        flash("Error generating image.", "danger")
        return redirect(url_for('content_bp.generate_text'))


# =========================================================
# 3. SAVE SCHEDULE ROUTE (Inserts topic to DB)
# =========================================================
@content_bp.route('/save_schedule', methods=['POST'])
@login_required
def save_schedule():
    print("\n=== [DEBUG] /save_schedule ===")

    if 'linkedin_token' not in session:
        flash("Please connect to LinkedIn first.", "warning")
        return redirect(url_for('auth_bp.linkedin_login'))

    author_urn = session.get('linkedin_user_urn')
    total_posts = int(request.form.get('total_posts', 0))
    added_by = "AI Generator"
    saved_count = 0
    
    cursor = mysql.connection.cursor()
    # Use current_app.root_path here
    FINAL_UPLOAD_FOLDER = os.path.join(current_app.root_path, "static", "uploaded_post_img")
    os.makedirs(FINAL_UPLOAD_FOLDER, exist_ok=True)

    for i in range(1, total_posts + 1):
        post_date = request.form.get(f'post_date_{i}')
        post_content = request.form.get(f'post_content_{i}')
        
        # 1. RETRIEVE TOPIC FROM FORM
        post_topic = request.form.get(f'post_topic_{i}', '')

        # Images
        gen_img_name = request.form.get(f'post_image_{i}', "").strip()
        manual_files = request.files.getlist(f'manual_image_{i}')

        if post_date and post_content:
            try:
                # 2. INSERT INTO DB WITH TOPIC_CONTEXT
                cursor.execute("""
                    INSERT INTO scheduled_posts 
                    (post_date, content, topic_context, added_by, author_urn, posted, added_date, updated_date)
                    VALUES (%s, %s, %s, %s, %s, 0, NOW(), NOW())
                """, (post_date, post_content.strip(), post_topic, added_by, author_urn))
                
                mysql.connection.commit()
                post_id = cursor.lastrowid
                
                # --- Image Logic ---
                final_filenames = []
                has_manual_files = any(f.filename for f in manual_files)

                if has_manual_files:
                    for file in manual_files:
                        if file and file.filename:
                            fname = secure_filename(file.filename)
                            new_name = f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}_{post_id}{os.path.splitext(fname)[1]}"
                            file.save(os.path.join(FINAL_UPLOAD_FOLDER, new_name))
                            final_filenames.append(new_name)
                elif gen_img_name:
                    # Use current_app.root_path for source image too
                    src_path = os.path.join(current_app.root_path, "static", "generated_image", gen_img_name)
                    if os.path.exists(src_path):
                        new_name = f"ai_{datetime.now().strftime('%Y%m%d%H%M%S')}_{post_id}.png"
                        shutil.copy(src_path, os.path.join(FINAL_UPLOAD_FOLDER, new_name))
                        final_filenames.append(new_name)
                
                if final_filenames:
                    csv_images = ",".join(final_filenames)
                    cursor.execute("UPDATE scheduled_posts SET image=%s WHERE id=%s", (csv_images, post_id))
                    mysql.connection.commit()

                saved_count += 1

            except Exception as e:
                print(f"[ERROR] Failed to save post {i}: {e}")
                continue

    cursor.close()
    flash(f"✅ {saved_count} posts saved successfully!", "success")
    return redirect(url_for('post_bp.view_posts'))


# =========================================================
# 4. CLEAR GENERATION
# =========================================================
@content_bp.route('/clear_and_generate')
@login_required
def clear_and_generate():
    """Clear previous generation and start fresh"""
    if 'daywise_content' in session:
        session.pop('daywise_content')
    return redirect(url_for('content_bp.generate_text'))