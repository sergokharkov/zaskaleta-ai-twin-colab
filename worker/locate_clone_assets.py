import argparse
import json
from pathlib import Path


def find_unique(root: Path, filename: str):
    matches = list(root.rglob(filename))
    if not matches:
        return None
    return matches[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mydrive', default='/content/drive/MyDrive')
    ap.add_argument('--profile', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    root = Path(args.mydrive)
    profile = json.loads(Path(args.profile).read_text(encoding='utf-8'))
    voice = find_unique(root, profile['master_voice_filename'])
    if voice is None:
        raise SystemExit(f"Master voice not found under {root}: {profile['master_voice_filename']}")

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
