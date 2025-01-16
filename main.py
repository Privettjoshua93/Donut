from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
import json
import re
from app import process_image_audio
from app2 import process_video_audio

app = Flask(__name__)

# Get Google Drive Folder ID and Credentials from Environment Variables
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')

# Ensure credentials are available
if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_DRIVE_FOLDER_ID:
    raise Exception("Google Drive credentials or folder ID not set in environment variables.")

@app.route('/create_video', methods=['POST'])
def create_video():
    data = request.json

    image_url = data.get('image_url')
    video_url = data.get('video_url')
    audio_url = data.get('audio_url')

    if not audio_url:
        return jsonify({'error': 'audio_url is required.'}), 400

    if image_url:
        return process_image_audio(image_url, audio_url)
    elif video_url:
        return process_video_audio(video_url, audio_url)
    else:
        return jsonify({'error': 'Either image_url or video_url is required.'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)