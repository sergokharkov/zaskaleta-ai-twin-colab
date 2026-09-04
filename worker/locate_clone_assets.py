import argparse
import json
import re
from pathlib import Path


PRIORITY_BEHAVIOR_NAME = 'MASTER_BEHAVIOR_01.mp4'
MASTER_BEHAVIOR_GLOB = 'MASTER_BEHAVIOR_*.mp4'


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


def behavior_sort_key(path: Path):
    m = re.search(r'MASTER_BEHAVIOR_(\d+)', path.stem, re.I)
    return (int(m.group(1)) if m else 9999, path.name.casefold())


def discover_master_behaviors(root: Path):
    found = {}
    for search_root in candidate_roots(root):
        try:
            for p in search_root.rglob(MASTER_BEHAVIOR_GLOB):
                if p.is_file():
                    found.setdefault(p.name.casefold(), p)
        except OSError:
            continue
    return sorted(found.values(), key=behavior_sort_key)


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
    seen = set()

    # Persistent motion library. Any future MASTER_BEHAVIOR_03, 04... file placed in
    # SOURCE is picked up automatically without another code change.
    master_behaviors = discover_master_behaviors(root)
    for i, p in enumerate(master_behaviors, start=1):
        resolved = p.resolve()
        videos.append({
            'path': str(p),
            'filename': p.name,
            'role': ['motion_profile', 'identity_consistent_behavior', 'natural_body_motion'],
            'priority': i,
            'verified': True,
            'notes': 'User-approved real motion reference for the persistent clone.'
        })
        seen.add(resolved)

    priority = next((p for p in master_behaviors if p.name == PRIORITY_BEHAVIOR_NAME), None)
    if priority is None and master_behaviors:
        priority = master_behaviors[0]

    # Keep older verified behavior material as supporting references.
    for item in profile.get('behavior_videos', []):
        name = item['filename']
        p = base / name
        if not p.is_file():
            p = find_unique(root, name)
        if p and p.is_file() and p.resolve() not in seen:
            videos.append({'path': str(p), **item})
            seen.add(p.resolve())

    if len(photos) < 5:
        raise SystemExit(f'Only {len(photos)} master photos found; need at least 5')

    result = {
        'base_dir': str(base),
        'master_voice': str(voice),
        'master_photos': photos[:6],
        'behavior_videos': videos,
        'master_behavior_videos': [str(p) for p in master_behaviors],
        'primary_behavior': str(priority) if priority and priority.is_file() else None,
        'profile': profile,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('✅ Clone assets located')
    print('BASE:', base)
    print('VOICE:', voice.name)
    print('PHOTOS:', len(photos[:6]))
    print('VIDEOS:', len(videos))
    print('🎬 MASTER MOTION REFERENCES:', len(master_behaviors))
    for p in master_behaviors:
        print('   •', p.name)
    if priority and priority.is_file():
        print('⭐ PRIMARY BEHAVIOR:', priority.name)


if __name__ == '__main__':
    main()
