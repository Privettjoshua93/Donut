from flask import jsonify
import subprocess
import os
import requests
from werkzeug.utils import secure_filename
import json
import re
from urllib.parse import urlparse, parse_qs
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Get Google Drive Folder ID and Credentials from Environment Variables
# Use a separate folder ID for audio files
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
AUDIO_DRIVE_FOLDER_ID = os.environ.get('AUDIO_DRIVE_FOLDER_ID')

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

    # Handle Google Drive share links
    if "drive.google.com" in url:
        file_id = None

        # Handle different types of share links
        parsed_url = urlparse(url)

        if 'id=' in url:
            query = parse_qs(parsed_url.query)
            if 'id' in query:
                file_id = query['id'][0]
        else:
            path_segments = parsed_url.path.split('/')
            if 'd' in path_segments:
                file_id_index = path_segments.index('d') + 1
                if file_id_index < len(path_segments):
                    file_id = path_segments[file_id_index]
            elif 'file' in path_segments and 'u' in path_segments:
                # Handle open?id= links
                if 'open' in path_segments:
                    query = parse_qs(parsed_url.query)
                    if 'id' in query:
                        file_id = query['id'][0]

        if not file_id:
            # Try to extract from URL directly
            file_id_match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
            if file_id_match:
                file_id = file_id_match.group(1)

        if not file_id:
            return False  # Unable to extract file ID

        download_url = f'https://drive.google.com/uc?export=download&id={file_id}'
        response = session.get(download_url, stream=True)

        # Handle confirmation for large files
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                params = {'id': file_id, 'confirm': value}
                response = session.get('https://drive.google.com/uc?export=download', params=params, stream=True)
                break
    else:
        response = session.get(url, stream=True)

    if response.status_code == 200:
        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(32768):
                if chunk:
                    f.write(chunk)
        return True
    else:
        return False

def upload_audio_to_google_drive(file_path, file_name):
    file_metadata = {
        'name': file_name,
        'parents': [AUDIO_DRIVE_FOLDER_ID]
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

def extract_audio_from_video(video_url):
    # Extract the original file extension for the video
    video_filename = os.path.basename(urlparse(video_url).path)
    video_filename = secure_filename(video_filename)
    video_extension = os.path.splitext(video_filename)[1]
    if not video_extension:
        video_extension = '.mp4'  # Default to .mp4 if no extension
    video_basename = os.path.splitext(video_filename)[0]
    video_filename = f"{video_basename}{video_extension}"

    audio_filename = f"{video_basename}.mp3"

    video_path = os.path.join(TEMP_DIR, video_filename)
    audio_path = os.path.join(TEMP_DIR, audio_filename)

    # Download video
    if not download_file(video_url, video_path):
        return jsonify({'error': 'Failed to download video.'}), 400

    # Extract audio using FFmpeg
    ffmpeg_command = [
        './ffmpeg/ffmpeg', '-y',
        '-i', video_path,
        '-q:a', '0',
        '-map', 'a',
        audio_path
    ]

    result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode()
        return jsonify({'error': 'FFmpeg failed to extract audio from video.', 'details': error_msg}), 500

    # Upload the audio to Google Drive
    shareable_link = upload_audio_to_google_drive(audio_path, audio_filename)

    # Clean up files
    os.remove(video_path)
    os.remove(audio_path)

    return jsonify({'audio_link': shareable_link}), 200
