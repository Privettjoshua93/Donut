from flask import Flask, request, jsonify
import subprocess
import os
import requests
from werkzeug.utils import secure_filename
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import json
import re

app = Flask(__name__)

# Get Google Drive Folder ID and Credentials from Environment Variables
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')

# Ensure credentials are available
if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_DRIVE_FOLDER_ID:
    raise Exception("Google Drive credentials or folder ID not set in environment variables.")

# Load credentials
credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    credentials_info,
    scopes=['https://www.googleapis.com/auth/drive']
)
drive_service = build('drive', 'v3', credentials=credentials)

# Ensure a temporary directory exists
TEMP_DIR = 'temp'
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

def download_file(url, dest_path):
    session = requests.Session()

    # Handle Google Drive download URLs
    if "drive.google.com" in url:
        file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if file_id_match:
            file_id = file_id_match.group(1)
        else:
            file_id_match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
            if file_id_match:
                file_id = file_id_match.group(1)
            else:
                return False  # Unable to extract file ID

        download_url = f'https://drive.google.com/uc?export=download&id={file_id}'
        response = session.get(download_url, stream=True)
    else:
        response = session.get(url, stream=True)

    if response.status_code == 200:
        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        return True
    else:
        return False

def upload_to_google_drive(file_path, file_name):
    file_metadata = {
        'name': file_name,
        'parents': [GOOGLE_DRIVE_FOLDER_ID]
    }
    media = MediaFileUpload(file_path, resumable=True)

    # Upload the file
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webContentLink, webViewLink'
    ).execute()

    # Make the file shareable
    permission = {
        'type': 'anyone',
        'role': 'reader',
    }
    drive_service.permissions().create(
        fileId=file['id'],
        body=permission,
    ).execute()

    # Return the shareable link
    return file.get('webContentLink')

@app.route('/create_video', methods=['POST'])
def create_video():
    data = request.json

    image_url = data.get('image_url')
    audio_url = data.get('audio_url')

    if not image_url or not audio_url:
        return jsonify({'error': 'image_url and audio_url are required.'}), 400

    # Create secure filenames
    image_filename = secure_filename(image_url.split('/')[-1])
    audio_filename = secure_filename(audio_url.split('/')[-1])
    output_filename = secure_filename(f"output_{os.getpid()}.mp4")

    image_path = os.path.join(TEMP_DIR, image_filename)
    audio_path = os.path.join(TEMP_DIR, audio_filename)
    output_path = os.path.join(TEMP_DIR, output_filename)

    # Download image
    if not download_file(image_url, image_path):
        return jsonify({'error': 'Failed to download image.'}), 400

    # Download audio
    if not download_file(audio_url, audio_path):
        return jsonify({'error': 'Failed to download audio.'}), 400

    # FFmpeg command to create video
    ffmpeg_command = [
    './ffmpeg/ffmpeg', '-y',
    '-loop', '1',
    '-i', image_path,
    '-i', audio_path,
    '-c:v', 'mpeg4',
    '-q:v', '1',
    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-shortest',
    output_path
]

    # Run the FFmpeg command
    result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return jsonify({'error': 'FFmpeg failed.', 'details': result.stderr.decode()}), 500

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, 'output.mp4')

    # Clean up files
    os.remove(image_path)
    os.remove(audio_path)
    os.remove(output_path)

    return jsonify({'video_link': shareable_link}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
