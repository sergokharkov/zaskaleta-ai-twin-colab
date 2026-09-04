import argparse
import json
import mimetypes
import os
import re
from pathlib import Path


def folder_id_from_value(value: str) -> str:
    value = (value or '').strip()
    if not value:
        return ''
    m = re.search(r'/folders/([A-Za-z0-9_-]+)', value)
    return m.group(1) if m else value


def main():
    ap = argparse.ArgumentParser(description='Upload completed AI Twin episode artifacts to a second Google Drive folder')
    ap.add_argument('--folder', required=True, help='Second Drive folder URL or folder ID')
    ap.add_argument('--episode-dir', required=True)
    ap.add_argument('--final', required=True)
    ap.add_argument('--day', type=int, required=True)
    args = ap.parse_args()

    folder_id = folder_id_from_value(args.folder)
    if not folder_id:
        raise RuntimeError('Archive folder ID is empty')

    # This script is intended to run under the Colab system Python after
    # google.colab.auth.authenticate_user() has authorized the archive account.
    import google.auth
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive.file'])
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)

    episode_dir = Path(args.episode_dir)
    final = Path(args.final)
    if not final.is_file():
        raise FileNotFoundError(final)

    files = [final]
    for name in ['episode.json', 'scene_prompts.json', 'checkpoint.json']:
        p = episode_dir / name
        if p.is_file():
            files.append(p)
    speech = episode_dir / 'scene_speech' / 'scene_speech_manifest.json'
    if speech.is_file():
        files.append(speech)

    uploaded = []
    for p in files:
        mime = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        metadata = {
            'name': f'Day_{args.day:02d}__{p.name}',
            'parents': [folder_id],
        }
        media = MediaFileUpload(str(p), mimetype=mime, resumable=True)
        created = service.files().create(
            body=metadata,
            media_body=media,
            fields='id,name,size,md5Checksum,parents',
            supportsAllDrives=True,
        ).execute()
        uploaded.append(created)
        print(f"✅ Archive upload: {created.get('name')} ({created.get('id')})")

    manifest_path = episode_dir / 'archive_second_drive.json'
    manifest_path.write_text(
        json.dumps({'folder_id': folder_id, 'uploaded': uploaded}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print('ARCHIVE_MANIFEST=' + str(manifest_path))


if __name__ == '__main__':
    main()
