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

    # Error if both image_url and video_url are provided
    if image_url and video_url:
        return jsonify({'error': 'Please provide either image_url or video_url, not both.'}), 400

    # Case 1: Only video_url is provided (extract audio)
    if video_url and not audio_url and not image_url:
        return extract_audio_from_video(video_url)

    # Case 2: image_url and audio_url are provided (create video from image and audio)
    if image_url and audio_url and not video_url:
        return process_image_audio(image_url, audio_url)

    # Case 3: video_url and audio_url are provided (combine video and audio)
    if video_url and audio_url and not image_url:
        return create_video_from_video(video_url, audio_url)

    # Error if neither image_url nor video_url are provided
    if not image_url and not video_url:
        return jsonify({'error': 'Either image_url or video_url must be provided.'}), 400

    # Error if audio_url is missing when required
    if (image_url or video_url) and not audio_url:
        return jsonify({'error': 'audio_url is required when providing image_url or video_url for video creation.'}), 400

    # General error for any other invalid cases
    return jsonify({'error': 'Invalid parameters provided.'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
