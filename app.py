from flask import Flask, request, jsonify
import subprocess
import os
import requests
from werkzeug.utils import secure_filename
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# Get Google Drive Folder ID and Credentials from Environment Variables
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

# Ensure credentials are available
if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_DRIVE_FOLDER_ID:
    raise Exception(
        "Google Drive credentials or folder ID not set in environment variables."
    )

# Load credentials
credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    credentials_info, scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build("drive", "v3", credentials=credentials)

# Ensure a temporary directory exists
TEMP_DIR = "temp"
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)


def download_file(url, dest_path):
    session = requests.Session()

    # Handle Google Drive download URLs
    if "drive.google.com" in url:
        file_id_match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if file_id_match:
            file_id = file_id_match.group(1)
        else:
            file_id_match = re.search(r"id=([a-zA-Z0-9_-]+)", url)
            if file_id_match:
                file_id = file_id_match.group(1)
            else:
                return False  # Unable to extract file ID

        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        response = session.get(download_url, stream=True)
    else:
        response = session.get(url, stream=True)

    if response.status_code == 200:
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        return True
    else:
        return False


def upload_to_google_drive(file_path, file_name):
    file_metadata = {"name": file_name, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, resumable=True)

    # Upload the file
    file = (
        drive_service.files()
        .create(body=file_metadata, media_body=media, fields="id, webContentLink, webViewLink")
        .execute()
    )

    # Make the file shareable
    permission = {"type": "anyone", "role": "reader"}
    drive_service.permissions().create(fileId=file["id"], body=permission).execute()

    # Return the shareable link
    return file.get("webContentLink")


def get_media_duration(media_path):
    result = subprocess.run(
        [
            "./ffmpeg/ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            media_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    duration_str = result.stdout.decode().strip()
    try:
        return float(duration_str)
    except ValueError:
        return None


@app.route("/create_video", methods=["POST"])
def create_video():
    data = request.json

    image_url = data.get("image_url")
    video_url = data.get("video_url")
    audio_url = data.get("audio_url")

    if not audio_url:
        return jsonify({"error": "audio_url is required."}), 400

    if not image_url and not video_url:
        return jsonify({"error": "Either image_url or video_url is required."}), 400

    audio_filename = secure_filename(audio_url.split("/")[-1])
    audio_path = os.path.join(TEMP_DIR, f"audio_{os.getpid()}_{audio_filename}")

    if not download_file(audio_url, audio_path):
        return jsonify({"error": "Failed to download audio."}), 400

    output_filename = secure_filename(f"output_{os.getpid()}.mp4")
    output_path = os.path.join(TEMP_DIR, output_filename)

    if image_url:
        # Image processing path
        image_filename = secure_filename(image_url.split("/")[-1])
        image_path = os.path.join(TEMP_DIR, f"image_{os.getpid()}_{image_filename}")

        if not download_file(image_url, image_path):
            os.remove(audio_path)
            return jsonify({"error": "Failed to download image."}), 400

        # Get audio duration
        duration = get_media_duration(audio_path)
        if duration is None:
            os.remove(audio_path)
            os.remove(image_path)
            return jsonify({"error": "Failed to get audio duration."}), 500

        # FFmpeg command to create video from image and audio without flashing issue
        ffmpeg_command = [
            "./ffmpeg/ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            image_path,
            "-i",
            audio_path,
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            "-movflags",
            "+faststart",
            "-vf",
            "format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-r",
            "30",
            "-t",
            str(duration),
            output_path,
        ]

        # Run the FFmpeg command
        result = subprocess.run(
            ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            os.remove(audio_path)
            os.remove(image_path)
            return (
                jsonify({"error": "FFmpeg failed to create video.", "details": error_msg}),
                500,
            )

        # Clean up image file
        os.remove(image_path)

    elif video_url:
        # Video processing path
        video_filename = secure_filename(video_url.split("/")[-1])
        video_path = os.path.join(TEMP_DIR, f"video_{os.getpid()}_{video_filename}")

        if not download_file(video_url, video_path):
            os.remove(audio_path)
            return jsonify({"error": "Failed to download video."}), 400

        # Mute original video
        muted_video_path = os.path.join(
            TEMP_DIR, f"muted_video_{os.getpid()}.mp4"
        )
        ffmpeg_mute_command = [
            "./ffmpeg/ffmpeg",
            "-y",
            "-i",
            video_path,
            "-c:v",
            "copy",
            "-an",
            muted_video_path,
        ]
        result = subprocess.run(
            ffmpeg_mute_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            os.remove(audio_path)
            os.remove(video_path)
            return (
                jsonify({"error": "FFmpeg failed to mute video.", "details": error_msg}),
                500,
            )

        # Get durations
        video_duration = get_media_duration(muted_video_path)
        if video_duration is None:
            os.remove(audio_path)
            os.remove(video_path)
            os.remove(muted_video_path)
            return jsonify({"error": "Failed to get video duration."}), 500

        audio_duration = get_media_duration(audio_path)
        if audio_duration is None:
            os.remove(audio_path)
            os.remove(video_path)
            os.remove(muted_video_path)
            return jsonify({"error": "Failed to get audio duration."}), 500

        # Calculate loop count
        loop_count = int(audio_duration // video_duration) + 1

        # Create a temporary text file listing the video files to concatenate
        concat_list_path = os.path.join(TEMP_DIR, f"concat_list_{os.getpid()}.txt")
        with open(concat_list_path, "w") as f:
            for _ in range(loop_count):
                f.write(f"file '{muted_video_path}'\n")

        # Concatenate the video files
        concatenated_video_path = os.path.join(
            TEMP_DIR, f"concatenated_video_{os.getpid()}.mp4"
        )
        ffmpeg_concat_command = [
            "./ffmpeg/ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list_path,
            "-c",
            "copy",
            concatenated_video_path,
        ]
        result = subprocess.run(
            ffmpeg_concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            os.remove(audio_path)
            os.remove(video_path)
            os.remove(muted_video_path)
            os.remove(concat_list_path)
            return (
                jsonify(
                    {
                        "error": "FFmpeg failed to concatenate video.",
                        "details": error_msg,
                    }
                ),
                500,
            )

        # Trim the concatenated video to match the audio duration
        trimmed_video_path = os.path.join(
            TEMP_DIR, f"trimmed_video_{os.getpid()}.mp4"
        )
        ffmpeg_trim_command = [
            "./ffmpeg/ffmpeg",
            "-y",
            "-i",
            concatenated_video_path,
            "-t",
            str(audio_duration),
            "-c",
            "copy",
            trimmed_video_path,
        ]
        result = subprocess.run(
            ffmpeg_trim_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            os.remove(audio_path)
            os.remove(video_path)
            os.remove(muted_video_path)
            os.remove(concat_list_path)
            os.remove(concatenated_video_path)
            return (
                jsonify({"error": "FFmpeg failed to trim video.", "details": error_msg}),
                500,
            )

        # Combine the trimmed video with the audio
        ffmpeg_combine_command = [
            "./ffmpeg/ffmpeg",
            "-y",
            "-i",
            trimmed_video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            output_path,
        ]
        result = subprocess.run(
            ffmpeg_combine_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            error_msg = result.stderr.decode()
            os.remove(audio_path)
            os.remove(video_path)
            os.remove(muted_video_path)
            os.remove(concat_list_path)
            os.remove(concatenated_video_path)
            os.remove(trimmed_video_path)
            return (
                jsonify(
                    {
                        "error": "FFmpeg failed to combine video and audio.",
                        "details": error_msg,
                    }
                ),
                500,
            )

        # Clean up video files
        os.remove(video_path)
        os.remove(muted_video_path)
        os.remove(concat_list_path)
        os.remove(concatenated_video_path)
        os.remove(trimmed_video_path)

    else:
        os.remove(audio_path)
        return jsonify({"error": "Invalid input. Provide either image_url or video_url."}), 400

    # Upload the video to Google Drive
    shareable_link = upload_to_google_drive(output_path, "output.mp4")

    # Clean up audio and output files
    os.remove(audio_path)
    os.remove(output_path)

    return jsonify({"video_link": shareable_link}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
