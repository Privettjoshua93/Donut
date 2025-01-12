from flask import Flask, request, jsonify
import subprocess
import os
import requests
import threading
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
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')  # Add WEBHOOK_URL to your environment variables

# Ensure credentials are available
if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_DRIVE_FOLDER_ID or not WEBHOOK_URL:
    raise Exception("Missing environment variables.")

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

def process_video(image_url, audio_url):
    # Create secure filenames
    image_filename = secure_filename(image_url.split('/')[-1]) + '.jpg'
    audio_filename = secure_filename(audio_url.split('/')[-1]) + '.mp3'
    output_filename = secure_filename(f"output_{os.getpid()}.mp4")

    image_path = os.path.join(TEMP_DIR, image_filename)
    audio_path = os.path.join(TEMP_DIR, audio_filename)
    output_path = os.path.join(TEMP_DIR, output_filename)

    try:
        # Download image
        if not download_file(image_url, image_path):
            raise Exception('Failed to download image.')

        # Download audio
        if not download_file(audio_url, audio_path):
            raise Exception('Failed to download audio.')

        # FFmpeg command to create video
        ffmpeg_command = [
            './ffmpeg/ffmpeg', '-y',
            '-loop', '1',
            '-i', image_path,
            '-i', audio_path,
            '-c:v', 'libx264',
            '-tune', 'stillimage',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-pix_fmt', 'yuv420p',
            '-shortest',
            output_path
        ]

        # Run the FFmpeg command
        result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            error_msg = result.stderr.decode()
            print(f"FFmpeg failed: {error_msg}")
            send_callback({'error': 'FFmpeg failed.', 'details': error_msg})
            return

        # Upload the video to Google Drive
        shareable_link = upload_to_google_drive(output_path, 'output.mp4')

        # Send callback to Make.com
        send_callback({'video_link': shareable_link})

    except Exception as e:
        print(f"Error processing video: {str(e)}")
        send_callback({'error': str(e)})
    finally:
        # Clean up files
        if os.path.exists(image_path):
            os.remove(image_path)
        if os.path.exists(audio_path):
            os.remove(audio_path)
        if os.path.exists(output_path):
            os.remove(output_path)

def send_callback(data):
    try:
        response = requests.post(WEBHOOK_URL, json=data)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send callback: {e}")

@app.route('/create_video', methods=['POST'])
def create_video_endpoint():
    data = request.json

    image_url = data.get('image_url')
    audio_url = data.get('audio_url')

    if not image_url or not audio_url:
        return jsonify({'error': 'image_url and audio_url are required.'}), 400

    # Start background processing
    threading.Thread(target=process_video, args=(image_url, audio_url)).start()

    # Respond immediately
    return jsonify({'status': 'Processing started'}), 202

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
