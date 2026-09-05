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


def find_all(root: Path, filename: str):
    found = []
    seen = set()
    for search_root in candidate_roots(root):
        try:
            for p in search_root.rglob(filename):
                if not p.is_file():
                    continue
                try:
                    key = p.resolve()
                except OSError:
                    key = p.absolute()
                if key in seen:
                    continue
                seen.add(key)
                found.append(p)
        except OSError:
            continue
    return found


def find_unique(root: Path, filename: str):
    matches = find_all(root, filename)
    if not matches:
        return None
    return matches[0]


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


def load_motion_approval(profile_path: Path):
    package_path = profile_path.parent / 'master_clone_package.json'
    if not package_path.is_file():
        raise SystemExit('master_clone_package.json is required to authorize motion references')
    package = json.loads(package_path.read_text(encoding='utf-8'))
    motion = (package.get('components') or {}).get('motion') or {}
    learning = motion.get('learning_policy') or {}
    if learning.get('manual_approval_required') is not True or learning.get('approved_motion_only') is not True:
        raise SystemExit('Motion approval policy is missing or weakened')

    approved_names = []
    for key in ('primary_reference', 'supporting_reference'):
        value = motion.get(key)
        if isinstance(value, str) and value.strip():
            approved_names.append(value.strip())
    approved_names = list(dict.fromkeys(approved_names))
    if not approved_names:
        raise SystemExit('No explicitly approved MASTER_BEHAVIOR references are configured')
    return package_path, approved_names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mydrive', default='/content/drive/MyDrive')
    ap.add_argument('--profile', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    root = Path(args.mydrive)
    profile_path = Path(args.profile)
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    package_path, approved_behavior_names = load_motion_approval(profile_path)

    voice = find_voice(root, profile['master_voice_filename'])
    if voice is None:
        raise SystemExit(
            f"Master voice not found under {root}: {profile['master_voice_filename']}\n"
            "The mounted storage likely does not contain the AI Twin source assets. "
            "Reconnect or synchronize the PRIMARY clone storage, then retry."
        )

    base = voice.parent
    photos = []
    for name in profile['photo_filenames']:
        p = base / name
        if not p.is_file():
            p = find_unique(root, name)
        if p and p.is_file():
            photos.append(str(p))

    canonical_name = profile.get('canonical_identity_photo')
    if not canonical_name:
        raise SystemExit('clone_reference_profile.json must explicitly define canonical_identity_photo')
    canonical_matches = [p for p in photos if Path(p).name == canonical_name]
    if len(canonical_matches) != 1:
        raise SystemExit(
            f'Canonical identity must resolve to exactly one approved master photo: '
            f'{canonical_name!r}; matches={len(canonical_matches)}'
        )

    discovered = discover_master_behaviors(root)
    discovered_by_name = {p.name: p for p in discovered}

    approved_behaviors = []
    missing_approved = []
    for name in approved_behavior_names:
        p = discovered_by_name.get(name) or find_unique(root, name)
        if p is None or not p.is_file():
            missing_approved.append(name)
            continue
        approved_behaviors.append(p)

    if missing_approved:
        raise SystemExit('Approved MASTER_BEHAVIOR assets missing: ' + ', '.join(missing_approved))

    # Any newly discovered MASTER_BEHAVIOR_* file is quarantined by default. Merely matching
    # the filename glob never grants approval and never makes it eligible for Clone v2 input.
    approved_name_set = set(approved_behavior_names)
    candidate_behaviors = [p for p in discovered if p.name not in approved_name_set]

    videos = []
    for i, p in enumerate(approved_behaviors, start=1):
        videos.append({
            'path': str(p),
            'filename': p.name,
            'role': ['motion_profile', 'identity_consistent_behavior', 'natural_body_motion'],
            'priority': i,
            'verified': True,
            'approved_for_master_clone': True,
            'approval_source': package_path.name,
            'notes': 'Explicitly allowlisted MASTER CLONE motion reference.'
        })

    # Legacy behavior_videos remain discoverable for audit/review only. "verified" is not
    # equivalent to APPROVED and therefore cannot make them runtime references automatically.
    legacy_behavior_candidates = []
    for item in profile.get('behavior_videos', []):
        name = item['filename']
        p = base / name
        if not p.is_file():
            p = find_unique(root, name)
        if p and p.is_file():
            legacy_behavior_candidates.append({'path': str(p), **item, 'approved_for_master_clone': False})

    if len(photos) < 5:
        raise SystemExit(f'Only {len(photos)} master photos found; need at least 5')

    priority_name = approved_behavior_names[0]
    priority = next((p for p in approved_behaviors if p.name == priority_name), None)
    if priority is None:
        raise SystemExit(f'Approved primary behavior is unavailable: {priority_name}')

    result = {
        'base_dir': str(base),
        'master_voice': str(voice),
        'master_photos': photos[:6],
        'behavior_videos': videos,
        'master_behavior_videos': [str(p) for p in approved_behaviors],
        'primary_behavior': str(priority),
        'candidate_behavior_videos': [str(p) for p in candidate_behaviors],
        'legacy_behavior_candidates': legacy_behavior_candidates,
        'motion_approval': {
            'policy_file': package_path.name,
            'approved_names': approved_behavior_names,
            'auto_discovery_grants_approval': False,
            'unapproved_candidates_quarantined': True,
        },
        'profile': profile,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('✅ Clone assets located with approval enforcement')
    print('BASE:', base)
    print('VOICE:', voice.name)
    print('PHOTOS:', len(photos[:6]))
    print('✅ APPROVED MASTER MOTION REFERENCES:', len(approved_behaviors))
    for p in approved_behaviors:
        print('   •', p.name)
    print('🧪 QUARANTINED MOTION CANDIDATES:', len(candidate_behaviors) + len(legacy_behavior_candidates))
    print('⭐ PRIMARY BEHAVIOR:', priority.name)


if __name__ == '__main__':
    main()
