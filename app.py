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

@app.route('/create_video', methods=['POST'])
def create_video():
    data = request.json

    image_url = data.get('image_url')
    video_url = data.get('video_url')
    audio_url = data.get('audio_url')

    if not audio_url:
        return jsonify({'error': 'audio_url is required.'}), 400

    if not image_url and not video_url:
        return jsonify({'error': 'Either image_url or video_url is required.'}), 400

    # Create secure filenames
    audio_filename = secure_filename(audio_url.split('/')[-1])
    audio_path = os.path.join(TEMP_DIR, audio_filename)

    output_filename = secure_filename(f"output_{os.getpid()}.mp4")
    output_path = os.path.join(TEMP_DIR, output_filename)

    # Download audio
    if not download_file(audio_url, audio_path):
        return jsonify({'error': 'Failed to download audio.'}), 400

    if video_url:
        video_filename = secure_filename(video_url.split('/')[-1])
        video_path = os.path.join(TEMP_DIR, video_filename)
        # Download video
        if not download_file(video_url, video_path):
            return jsonify({'error': 'Failed to download video.'}), 400

        # Extract existing audio from video
        existing_audio_path = os.path.join(TEMP_DIR, f"existing_audio_{os.getpid()}.aac")
        ffmpeg_extract_audio_command = [
            './ffmpeg/ffmpeg', '-y',
            '-i', video_path,
            '-vn',
            '-acodec', 'aac',
            existing_audio_path
        ]

        result = subprocess.run(ffmpeg_extract_audio_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            return jsonify({'error': 'FFmpeg failed to extract audio.', 'details': error_msg}), 500

        # Mix existing audio with new audio
        mixed_audio_path = os.path.join(TEMP_DIR, f"mixed_audio_{os.getpid()}.aac")
        ffmpeg_mix_audio_command = [
            './ffmpeg/ffmpeg', '-y',
            '-i', existing_audio_path,
            '-i', audio_path,
            '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=longest',
            '-c:a', 'aac',
            '-b:a', '192k',
            mixed_audio_path
        ]

        result = subprocess.run(ffmpeg_mix_audio_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            return jsonify({'error': 'FFmpeg failed to mix audio.', 'details': error_msg}), 500

        # Combine video with mixed audio
        ffmpeg_command = [
            './ffmpeg/ffmpeg', '-y',
            '-i', video_path,
            '-i', mixed_audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-movflags', '+faststart',
            '-map', '0:v:0',
            '-map', '1:a:0',
            output_path
        ]

        result = subprocess.run(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            return jsonify({'error': 'FFmpeg failed to combine video and audio.', 'details': error_msg}), 500

    else:
        image_filename = secure_filename(image_url.split('/')[-1])
        image_path = os.path.join(TEMP_DIR, image_filename)
        # Download image
        if not download_file(image_url, image_path):
            return jsonify({'error': 'Failed to download image.'}), 400

        # Get audio duration
        duration = get_audio_duration(audio_path)
        if duration is None:
            return jsonify({'error': 'Failed to get audio duration.'}), 500

        # FFmpeg command to create video from image and audio
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
            return jsonify({'error': 'FFmpeg failed to create video.', 'details': error_msg}), 500

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, 'output.mp4')

    # Clean up files
    os.remove(audio_path)
    os.remove(output_path)

    if video_url:
        os.remove(video_path)
        os.remove(existing_audio_path)
        os.remove(mixed_audio_path)
    else:
        os.remove(image_path)

    return jsonify({'video_link': shareable_link}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
