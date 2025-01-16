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

# Ensure a temporary directory exists
TEMP_DIR = 'temp'
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Get Google Drive Folder ID and Credentials from Environment Variables
GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

# Load credentials
credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    credentials_info,
    scopes=['https://www.googleapis.com/auth/drive']
)
drive_service = build('drive', 'v3', credentials=credentials)

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
        token = None
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
        if token:
            params = {'confirm': token}
            response = session.get(download_url, params=params, stream=True)
        else:
            response = session.get(download_url, stream=True)
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

def get_video_duration(video_path):
    """Get the duration of the video file in seconds."""
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
        return jsonify({'error': 'FFmpeg failed to combine video and audio.', 'details': error_msg}), 500

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, 'output.mp4')

    # Clean up files
    os.remove(video_path)
    os.remove(audio_path)
    os.remove(looped_video_path)
    os.remove(trimmed_video_path)
    os.remove(concat_list_path)
    os.remove(output_path)

    return jsonify({'video_link': shareable_link}), 200