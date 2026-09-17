import os
import json
import subprocess
import tempfile

import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_credentials():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    return Credentials.from_service_account_info(creds_json, scopes=SCOPES)


def main():
    creds = get_credentials()
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(os.environ["SHEET_ID"]).sheet1

    headers = ws.row_values(1)
    col_status = headers.index("video_status") + 1
    col_link = headers.index("video_link") + 1

    rows = ws.get_all_records()
    pexels_key = os.environ["PEXELS_API_KEY"]
    drive_service = build("drive", "v3", credentials=creds)

    processed = 0
    for i, row in enumerate(rows, start=2):  # baris 1 = header
        if str(row.get("video_status")).strip() != "pending":
            continue

        row_id = row["row_id"]
        script_text = str(row["script"])
        print(f"--- Memproses row_id {row_id} ---")

        try:
            with tempfile.TemporaryDirectory() as tmp:
                clip_path = os.path.join(tmp, "clip.mp4")
                audio_path = os.path.join(tmp, "voice.mp3")
                final_path = os.path.join(tmp, "final.mp4")

                # 1. Cari stock footage di Pexels
                r = requests.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": pexels_key},
                    params={"query": script_text, "per_page": 1, "orientation": "portrait"},
                    timeout=30,
                )
                r.raise_for_status()
                video_url = r.json()["videos"][0]["video_files"][0]["link"]

                with requests.get(video_url, stream=True, timeout=60) as vr:
                    with open(clip_path, "wb") as f:
                        for chunk in vr.iter_content(chunk_size=8192):
                            f.write(chunk)

                # 2. Generate voiceover gratis (edge-tts)
                subprocess.run(
                    [
                        "edge-tts",
                        "--voice", "en-US-AriaNeural",
                        "--text", script_text,
                        "--write-media", audio_path,
                    ],
                    check=True,
                )

                # 3. Gabungkan video + voiceover (ffmpeg sudah tersedia di GitHub Actions runner)
                subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-stream_loop", "-1", "-i", clip_path,
                        "-i", audio_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", final_path,
                    ],
                    check=True,
                )

                # 4. Upload hasil ke Google Drive
                metadata = {
                    "name": f"{row_id}_final.mp4",
                    "parents": [os.environ["DRIVE_FOLDER_ID"]],
                }
                media = MediaFileUpload(final_path, mimetype="video/mp4")
                uploaded = drive_service.files().create(
                    body=metadata, media_body=media, fields="id"
                ).execute()
                file_id = uploaded["id"]
                drive_service.permissions().create(
                    fileId=file_id, body={"role": "reader", "type": "anyone"}
                ).execute()
                video_link = f"https://drive.google.com/uc?id={file_id}&export=download"

                # 5. Update Google Sheet
                ws.update_cell(i, col_status, "done")
                ws.update_cell(i, col_link, video_link)
                print(f"Row {row_id} selesai -> {video_link}")
                processed += 1

        except Exception as e:
            print(f"GAGAL memproses row_id {row_id}: {e}")
            ws.update_cell(i, col_status, "error")

    print(f"Selesai. Total video diproses: {processed}")


if __name__ == "__main__":
    main()
