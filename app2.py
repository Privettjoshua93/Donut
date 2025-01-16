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

def get_duration(file_path):
    """Get the duration of a media file in seconds."""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
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

def create_video_from_video(video_url, audio_url):
    # Create secure filenames
    video_filename = secure_filename(video_url.split('/')[-1]) + '.mp4'
    audio_filename = secure_filename(audio_url.split('/')[-1]) + '.mp3'
    output_filename = f"output_{os.getpid()}.mp4"

    video_path = os.path.join(TEMP_DIR, video_filename)
    audio_path = os.path.join(TEMP_DIR, audio_filename)
    output_path = os.path.join(TEMP_DIR, output_filename)

    # Download video
    if not download_file(video_url, video_path):
        return jsonify({'error': 'Failed to download video.'}), 400

    # Download audio
    if not download_file(audio_url, audio_path):
        return jsonify({'error': 'Failed to download audio.'}), 400

    # Get durations
    video_duration = get_duration(video_path)
    audio_duration = get_duration(audio_path)

    if audio_duration is None:
        return jsonify({'error': 'Failed to get audio duration.'}), 500

    if video_duration is None:
        return jsonify({'error': 'Failed to get video duration.'}), 500

    # Calculate the number of loops needed
    loop_count = int(audio_duration // video_duration) + 1

    # FFmpeg command to process video and audio without trimming, looping video as needed
    ffmpeg_command = [
        'ffmpeg', '-y',
        '-stream_loop', str(loop_count - 1),
        '-i', video_path,
        '-i', audio_path,
        '-c:v', 'libx264',
        '-vf', "scale='min(1080,iw)':'min(1350,ih)',pad=1080:1350:(1080-iw)/2:(1350-ih)/2",
        '-c:a', 'aac',
        '-pix_fmt', 'yuv420p',
        '-b:a', '192k',
        '-shortest',
        output_path
    ]

    # Run the FFmpeg command
    result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode()
        return jsonify({'error': 'FFmpeg failed to process video and audio.', 'details': error_msg}), 500

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, 'output.mp4')

    # Clean up files
    os.remove(video_path)
    os.remove(audio_path)
    os.remove(output_path)

    return jsonify({'video_link': shareable_link}), 200