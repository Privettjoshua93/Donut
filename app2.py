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

        def get_confirm_token(response):
            for key, value in response.cookies.items():
                if key.startswith('download_warning'):
                    return value
            return None

        def save_response_content(response, destination):
            CHUNK_SIZE = 32768
            with open(destination, "wb") as f:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)

        base_url = "https://docs.google.com/uc?export=download"
        response = session.get(base_url, params={'id': file_id}, stream=True)
        token = get_confirm_token(response)

        if token:
            params = {'id': file_id, 'confirm': token}
            response = session.get(base_url, params=params, stream=True)

        if response.status_code == 200:
            save_response_content(response, dest_path)
            return True
        else:
            return False
    else:
        response = session.get(url, stream=True)

        if response.status_code == 200:
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(1024):
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
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webContentLink, webViewLink'
    ).execute()
    permission = {
        'type': 'anyone',
        'role': 'reader',
    }
    drive_service.permissions().create(
        fileId=file['id'],
        body=permission,
    ).execute()
    return file.get('webContentLink')

def get_audio_duration(audio_path):
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

def get_video_duration(video_path):
    result = subprocess.run(
        ['./ffmpeg/ffprobe', '-v', 'error', '-show_entries',
         'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
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

def process_video_audio(video_url, audio_url):
    # Create secure filenames
    video_filename = secure_filename(video_url.split('/')[-1] + '.mp4')
    audio_filename = secure_filename(audio_url.split('/')[-1] + '.mp3')
    video_path = os.path.join(TEMP_DIR, video_filename)
    audio_path = os.path.join(TEMP_DIR, audio_filename)
    output_filename = f"output_{os.getpid()}.mp4"
    output_path = os.path.join(TEMP_DIR, output_filename)

    # Download video
    if not download_file(video_url, video_path):
        return jsonify({'error': 'Failed to download video.'}), 400

    # Download audio
    if not download_file(audio_url, audio_path):
        return jsonify({'error': 'Failed to download audio.'}), 400

    # Get durations
    audio_duration = get_audio_duration(audio_path)
    video_duration = get_video_duration(video_path)
    if audio_duration is None or video_duration is None:
        return jsonify({'error': 'Failed to get durations.'}), 500

    # Calculate loop count
    loop_count = int(audio_duration // video_duration) + 1

    # Create concat list file
    concat_list_path = os.path.join(TEMP_DIR, f"concat_list_{os.getpid()}.txt")
    with open(concat_list_path, 'w') as f:
        for _ in range(loop_count):
            f.write(f"file '{video_path}'\n")

    # Concatenate videos
    looped_video_path = os.path.join(TEMP_DIR, f"looped_video_{os.getpid()}.mp4")
    ffmpeg_concat_command = [
        './ffmpeg/ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', concat_list_path,
        '-c', 'copy',
        looped_video_path
    ]
    result = subprocess.run(ffmpeg_concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode()
        print(f"FFmpeg concat error: {error_msg}")
        return jsonify({'error': 'FFmpeg failed to concatenate video.', 'details': error_msg}), 500

    # Trim video to match audio duration
    trimmed_video_path = os.path.join(TEMP_DIR, f"trimmed_video_{os.getpid()}.mp4")
    ffmpeg_trim_command = [
        './ffmpeg/ffmpeg', '-y',
        '-i', looped_video_path,
        '-t', str(audio_duration),
        '-c:v', 'copy',
        trimmed_video_path
    ]
    result = subprocess.run(ffmpeg_trim_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode()
        print(f"FFmpeg trim error: {error_msg}")
        return jsonify({'error': 'FFmpeg failed to trim video.', 'details': error_msg}), 500

    # Combine video with new audio, muting original audio
    ffmpeg_command = [
        './ffmpeg/ffmpeg', '-y',
        '-i', trimmed_video_path,
        '-i', audio_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-shortest',
        '-movflags', '+faststart',
        output_path
    ]
    result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode()
        print(f"FFmpeg combine error: {error_msg}")
        return jsonify({'error': 'FFmpeg failed to create video.', 'details': error_msg}), 500

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, 'output.mp4')

    # Clean up files
    os.remove(video_path)
    os.remove(audio_path)
    os.remove(output_path)
    os.remove(looped_video_path)
    os.remove(trimmed_video_path)
    os.remove(concat_list_path)

    return jsonify({'video_link': shareable_link}), 200