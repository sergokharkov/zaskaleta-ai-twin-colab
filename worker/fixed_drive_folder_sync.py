import argparse
import hashlib
import io
import mimetypes
from pathlib import Path

FOLDER_MIME = 'application/vnd.google-apps.folder'


def build_service():
    import google.auth
    from googleapiclient.discovery import build
    creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def list_children(service, folder_id):
    out = []
    page = None
    while True:
        r = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields='nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)',
            pageToken=page,
            pageSize=1000,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        out.extend(r.get('files', []))
        page = r.get('nextPageToken')
        if not page:
            break
    return out


def ensure_access(service, folder_id):
    meta = service.files().get(
        fileId=folder_id,
        fields='id,name,mimeType,capabilities(canEdit,canAddChildren)',
        supportsAllDrives=True,
    ).execute()
    if meta.get('mimeType') != FOLDER_MIME:
        raise RuntimeError(f'ID {folder_id} is not a Google Drive folder')
    return meta


def download_file(service, file_id, dst):
    from googleapiclient.http import MediaIoBaseDownload
    dst.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dst, 'wb') as fh:
        dl = MediaIoBaseDownload(fh, request, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()


def pull_tree(service, remote_folder_id, local_dir):
    local_dir.mkdir(parents=True, exist_ok=True)
    for item in list_children(service, remote_folder_id):
        name = item['name']
        target = local_dir / name
        if item['mimeType'] == FOLDER_MIME:
            pull_tree(service, item['id'], target)
        else:
            # Native Google Docs are not AI Twin binary assets; skip them.
            if item['mimeType'].startswith('application/vnd.google-apps.'):
                continue
            remote_size = int(item.get('size') or 0)
            if target.is_file() and remote_size and target.stat().st_size == remote_size:
                continue
            print(f'⬇️ Drive pull: {target.relative_to(local_dir.parent)}')
            download_file(service, item['id'], target)


def create_folder(service, parent_id, name):
    body = {'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]}
    return service.files().create(body=body, fields='id,name', supportsAllDrives=True).execute()['id']


def upload_file(service, parent_id, path, existing=None):
    from googleapiclient.http import MediaFileUpload
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True, chunksize=16 * 1024 * 1024)
    if existing:
        return service.files().update(
            fileId=existing['id'], media_body=media,
            fields='id,name,size,md5Checksum', supportsAllDrives=True
        ).execute()
    return service.files().create(
        body={'name': path.name, 'parents': [parent_id]}, media_body=media,
        fields='id,name,size,md5Checksum', supportsAllDrives=True
    ).execute()


def push_tree(service, local_dir, remote_folder_id):
    children = {x['name']: x for x in list_children(service, remote_folder_id)}
    for path in sorted(local_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if path.name.startswith('.'):
            continue
        remote = children.get(path.name)
        if path.is_dir():
            if remote and remote['mimeType'] != FOLDER_MIME:
                raise RuntimeError(f'Remote name collision: {path.name}')
            child_id = remote['id'] if remote else create_folder(service, remote_folder_id, path.name)
            push_tree(service, path, child_id)
        elif path.is_file():
            if remote and remote['mimeType'] == FOLDER_MIME:
                raise RuntimeError(f'Remote name collision: {path.name}')
            same = False
            if remote:
                try:
                    same = int(remote.get('size') or -1) == path.stat().st_size
                except Exception:
                    same = False
            if same:
                continue
            print(f'⬆️ Drive push: {path.relative_to(local_dir.parent)}')
            upload_file(service, remote_folder_id, path, remote)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['check', 'pull', 'push'])
    ap.add_argument('--folder-id', required=True)
    ap.add_argument('--local-dir', required=True)
    args = ap.parse_args()
    service = build_service()
    meta = ensure_access(service, args.folder_id)
    print(f"✅ Fixed Drive folder accessible: {meta.get('name')} ({meta.get('id')})")
    local_dir = Path(args.local_dir)
    if args.mode == 'check':
        return
    if args.mode == 'pull':
        pull_tree(service, args.folder_id, local_dir)
        print('✅ Fixed source folder synchronized to local runtime')
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
        push_tree(service, local_dir, args.folder_id)
        print('✅ Runtime progress synchronized back to fixed source folder')


if __name__ == '__main__':
    main()
