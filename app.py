from flask import jsonify
import subprocess
import os
import requests
from werkzeug.utils import secure_filename
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Get Google Drive Folder ID and Credentials from Environment Variables
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')

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

def get_audio_duration(audio_path):
    """Get the duration of the audio file in seconds."""
    result = subprocess.run(
        ['./ffmpeg/ffprobe', '-v', 'error', '-show_entries',
         'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        return None
    duration_str = result.stdout.decode().strip()
    try:
        return float(duration_str)
    except ValueError:
        return None

def process_image_audio(image_url, audio_url):
    # Create secure filenames
    image_filename = secure_filename(image_url.split('/')[-1]) + '.jpg'
    audio_filename = secure_filename(audio_url.split('/')[-1]) + '.mp3'
    output_filename = f"output_{os.getpid()}.mp4"

    image_path = os.path.join(TEMP_DIR, image_filename)
    audio_path = os.path.join(TEMP_DIR, audio_filename)
    output_path = os.path.join(TEMP_DIR, output_filename)

    # Download image
    if not download_file(image_url, image_path):
        return jsonify({'error': 'Failed to download image.'}), 400

    # Download audio
    if not download_file(audio_url, audio_path):
        return jsonify({'error': 'Failed to download audio.'}), 400

    # Get audio duration
    duration = get_audio_duration(audio_path)
    if duration is None:
        return jsonify({'error': 'Failed to get audio duration.'}), 500

    # FFmpeg command to create video
    ffmpeg_command = [
        './ffmpeg/ffmpeg', '-y',
        '-loop', '1',
        '-i', image_path,
        '-i', audio_path,
        '-c:v', 'libx264',
        '-tune', 'stillimage',
        '-profile:v', 'high',
        '-level', '4.2',
        '-vf', "scale='min(1080,iw)':'min(1350,ih)',pad=1080:1350:(1080-iw)/2:(1350-ih)/2,format=yuv420p",
        '-c:a', 'aac',
        '-b:a', '192k',
        '-ac', '2',
        '-ar', '48000',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        '-r', '30',
        '-t', str(duration),
        '-shortest',
        output_path
    ]

    # Run the FFmpeg command
    result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode()
        return jsonify({'error': 'FFmpeg failed.', 'details': error_msg}), 500

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, 'output.mp4')

    # Clean up files
    os.remove(image_path)
    os.remove(audio_path)
    os.remove(output_path)

    return jsonify({'video_link': shareable_link}), 200