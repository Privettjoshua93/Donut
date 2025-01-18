from flask import Flask, request, jsonify
import os
from app import process_image_audio
from app2 import create_video_from_video
from app3 import extract_audio_from_video

app = Flask(__name__)

# Get Google Drive Folder ID and Credentials from Environment Variables
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')  # For video outputs
AUDIO_DRIVE_FOLDER_ID = os.environ.get('AUDIO_DRIVE_FOLDER_ID')    # For audio outputs

# Ensure credentials are available
if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_DRIVE_FOLDER_ID or not AUDIO_DRIVE_FOLDER_ID:
    raise Exception("Google Drive credentials or folder IDs not set in environment variables.")

@app.route('/create_video', methods=['POST'])
def create_video():
    data = request.json

    image_url = data.get('image_url')
    video_url = data.get('video_url')
    audio_url = data.get('audio_url')

    if not audio_url:
        return jsonify({'error': 'audio_url is required.'}), 400

    if image_url and video_url:
        return jsonify({'error': 'Please provide either image_url or video_url, not both.'}), 400

    if image_url:
        return process_image_audio(image_url, audio_url)
    elif video_url:
        return create_video_from_video(video_url, audio_url)
    else:
        return jsonify({'error': 'Either image_url or video_url must be provided.'}), 400

@app.route('/extract_audio', methods=['POST'])
def extract_audio():
    data = request.json

    video_url = data.get('video_url')

    if not video_url:
        return jsonify({'error': 'video_url is required.'}), 400

    return extract_audio_from_video(video_url)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
