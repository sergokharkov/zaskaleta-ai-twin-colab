import argparse
import json
import mimetypes
import re
from pathlib import Path


def folder_id_from_value(value: str) -> str:
    value = (value or '').strip()
    if not value:
        return ''
    m = re.search(r'/folders/([A-Za-z0-9_-]+)', value)
    return m.group(1) if m else value


def main():
    ap = argparse.ArgumentParser(description='Upload completed AI Twin episode artifacts to the fixed archive Google Drive folder')
    ap.add_argument('--folder', required=True, help='Archive Drive folder URL or folder ID')
    ap.add_argument('--episode-dir', required=True)
    ap.add_argument('--final', required=True)
    ap.add_argument('--day', type=int, required=True)
    args = ap.parse_args()

    folder_id = folder_id_from_value(args.folder)
    if not folder_id:
        raise RuntimeError('Archive folder ID is empty')

    import google.auth
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive'])
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    folder = service.files().get(
        fileId=folder_id,
        fields='id,name,mimeType,capabilities(canAddChildren)',
        supportsAllDrives=True,
    ).execute()
    if folder.get('mimeType') != 'application/vnd.google-apps.folder':
        raise RuntimeError('Archive ID is not a Google Drive folder')
    if folder.get('capabilities', {}).get('canAddChildren') is False:
        raise PermissionError('Current Google account has no write access to the fixed archive folder')
    print(f"✅ Fixed archive folder accessible: {folder.get('name')} ({folder_id})")

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
        archive_name = f'Day_{args.day:02d}__{p.name}'
        existing = service.files().list(
            q=f"'{folder_id}' in parents and name='{archive_name.replace(chr(39), chr(92)+chr(39))}' and trashed=false",
            fields='files(id,name,size)',
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute().get('files', [])
        media = MediaFileUpload(str(p), mimetype=mime, resumable=True)
        if existing:
            created = service.files().update(
                fileId=existing[0]['id'], media_body=media,
                fields='id,name,size,md5Checksum,parents', supportsAllDrives=True,
            ).execute()
        else:
            created = service.files().create(
                body={'name': archive_name, 'parents': [folder_id]},
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
