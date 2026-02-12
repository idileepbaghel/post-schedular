from app import create_app

app = create_app()

if __name__ == '__main__':
    # Ensure static folders exist
    import os
    os.makedirs(os.path.join('app', 'static', 'generated_image'), exist_ok=True)
    os.makedirs(os.path.join('app', 'static', 'uploaded_post_img'), exist_ok=True)
    
    app.run(debug=True, port=5500)