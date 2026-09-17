import os
import json
from datetime import datetime, timedelta, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
BUFFER_ENDPOINT = "https://api.buffer.com"  # Buffer GraphQL API (REST lama sudah dimatikan)


def get_credentials():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    return Credentials.from_service_account_info(creds_json, scopes=SCOPES)


def buffer_graphql(query, token):
    resp = requests.post(
        BUFFER_ENDPOINT,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        json={"query": query},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    creds = get_credentials()
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(os.environ["SHEET_ID"]).sheet1

    headers = ws.row_values(1)
    col_buffer_status = headers.index("buffer_status") + 1

    rows = ws.get_all_records()
    token = os.environ["BUFFER_ACCESS_TOKEN"]
    channel_ids = [c.strip() for c in os.environ["BUFFER_CHANNEL_IDS"].split(",") if c.strip()]

    processed = 0
    for i, row in enumerate(rows, start=2):
        if str(row.get("video_status")).strip() != "done":
            continue
        if str(row.get("buffer_status")).strip() == "scheduled":
            continue

        row_id = row["row_id"]
        print(f"--- Menjadwalkan row_id {row_id} ---")

        try:
            text = f"{row['caption']} {row['hashtag']}".replace('"', '\\"')
            video_url = row["video_link"]

            # Jadwal di sheet dianggap WIB (UTC+7), Buffer butuh format UTC ISO 8601
            local_dt = datetime.strptime(
                f"{row['scheduled_date']} {row['scheduled_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone(timedelta(hours=7)))
            due_at = local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            success_count = 0
            for channel_id in channel_ids:
                mutation = f"""
                mutation CreatePost {{
                  createPost(input: {{
                    text: "{text}"
                    channelId: "{channel_id}"
                    schedulingType: automatic
                    mode: customScheduled
                    dueAt: "{due_at}"
                    assets: [{{ video: {{ url: "{video_url}" }} }}]
                  }}) {{
                    ... on PostActionSuccess {{ post {{ id }} }}
                    ... on MutationError {{ message }}
                  }}
                }}
                """
                result = buffer_graphql(mutation, token)
                print(row_id, channel_id, result)
                if result.get("data", {}).get("createPost", {}).get("post"):
                    success_count += 1

            if success_count == len(channel_ids):
                ws.update_cell(i, col_buffer_status, "scheduled")
                print(f"Row {row_id} berhasil dijadwalkan ke semua channel")
                processed += 1
            else:
                ws.update_cell(i, col_buffer_status, "error")
                print(f"Row {row_id} sebagian/semua channel gagal, cek log di atas")

        except Exception as e:
            print(f"GAGAL row {row_id}: {e}")
            ws.update_cell(i, col_buffer_status, "error")

    print(f"Selesai. Total dijadwalkan: {processed}")


if __name__ == "__main__":
    main()
