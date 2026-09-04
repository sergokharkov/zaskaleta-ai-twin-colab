import argparse
import json
from pathlib import Path


def candidate_roots(root: Path):
    roots = [root]
    drive_root = root.parent
    shortcuts = drive_root / '.shortcut-targets-by-id'
    if shortcuts.is_dir():
        roots.append(shortcuts)
    return roots


def find_unique(root: Path, filename: str):
    for search_root in candidate_roots(root):
        matches = list(search_root.rglob(filename))
        if matches:
            return matches[0]
    return None


def find_voice(root: Path, preferred_name: str):
    exact = find_unique(root, preferred_name)
    if exact:
        return exact

    preferred = Path(preferred_name)
    stem = preferred.stem.casefold()
    allowed = {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg'}
    for search_root in candidate_roots(root):
        try:
            for p in search_root.rglob('*'):
                if not p.is_file() or p.suffix.casefold() not in allowed:
                    continue
                pstem = p.stem.casefold()
                if pstem == stem or ('zaskaleta' in pstem and 'voice' in pstem and 'master' in pstem):
                    return p
        except OSError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mydrive', default='/content/drive/MyDrive')
    ap.add_argument('--profile', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    root = Path(args.mydrive)
    profile = json.loads(Path(args.profile).read_text(encoding='utf-8'))
    voice = find_voice(root, profile['master_voice_filename'])
    if voice is None:
        raise SystemExit(
            f"Master voice not found under {root}: {profile['master_voice_filename']}\n"
            "The mounted Google Drive likely does not contain the AI Twin source assets. "
            "Reconnect the PRIMARY Drive account, then retry."
        )

    base = voice.parent
    photos = []
    for name in profile['photo_filenames']:
        p = base / name
        if not p.is_file():
            p = find_unique(root, name)
        if p and p.is_file():
            photos.append(str(p))

    videos = []
    for item in profile.get('behavior_videos', []):
        name = item['filename']
        p = base / name
        if not p.is_file():
            p = find_unique(root, name)
        if p and p.is_file():
            videos.append({'path': str(p), **item})

    if len(photos) < 5:
        raise SystemExit(f'Only {len(photos)} master photos found; need at least 5')

    result = {
        'base_dir': str(base),
        'master_voice': str(voice),
        'master_photos': photos[:6],
        'behavior_videos': videos,
        'profile': profile,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('✅ Clone assets located')
    print('BASE:', base)
    print('VOICE:', voice.name)
    print('PHOTOS:', len(photos[:6]))
    print('VIDEOS:', len(videos))


if __name__ == '__main__':
    main()
