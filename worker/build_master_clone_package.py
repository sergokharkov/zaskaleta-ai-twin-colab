import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def ensure_dirs(root: Path):
    names = ['IDENTITY', 'MOTION', 'TALKING', 'VOICE', 'APPROVED', 'REJECTED', 'VERSIONS']
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path, package_root: Path):
    return {
        'path': str(path.relative_to(package_root)).replace(os.sep, '/'),
        'bytes': path.stat().st_size,
        'sha256': sha256_file(path),
    }


def copy_if_exists(src: Path, dst: Path):
    if src and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst
    return None


def atomic_write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def main():
    ap = argparse.ArgumentParser(description='Build the scene-independent Zaskaleta MASTER CLONE package')
    ap.add_argument('--asset-map', required=True, help='JSON produced by locate_clone_assets.py')
    ap.add_argument('--spec', required=True, help='content/master_clone_package.json')
    ap.add_argument('--output-root', required=True, help='Destination parent, typically Google Drive SOURCE')
    ap.add_argument('--version', default='v1')
    args = ap.parse_args()

    assets = json.loads(Path(args.asset_map).read_text(encoding='utf-8'))
    spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))

    package = Path(args.output_root) / 'MASTER_CLONE'
    ensure_dirs(package)

    identity_dir = package / 'IDENTITY'
    motion_dir = package / 'MOTION'
    voice_dir = package / 'VOICE'
    versions_dir = package / 'VERSIONS' / args.version
    versions_dir.mkdir(parents=True, exist_ok=True)

    canonical_name = spec['components']['identity']['canonical_anchor']
    photos = [Path(p) for p in assets.get('master_photos', []) if Path(p).is_file()]
    canonical = next((p for p in photos if p.name == canonical_name), photos[0] if photos else None)

    copied = {'identity': [], 'motion': [], 'voice': None}
    if canonical:
        target = identity_dir / ('CANONICAL_' + canonical.name)
        copied['identity'].append(copy_if_exists(canonical, target))
    for p in photos:
        if canonical and p.resolve() == canonical.resolve():
            continue
        copied['identity'].append(copy_if_exists(p, identity_dir / p.name))
    copied['identity'] = [x for x in copied['identity'] if x]

    for item in assets.get('master_behavior_videos', []):
        p = Path(item)
        copied_path = copy_if_exists(p, motion_dir / p.name)
        if copied_path:
            copied['motion'].append(copied_path)

    voice = Path(assets['master_voice']) if assets.get('master_voice') else None
    if voice and voice.is_file():
        copied['voice'] = copy_if_exists(voice, voice_dir / voice.name)

    profile = assets.get('profile', {})
    package_manifest = {
        'schema': 'zaskaleta-master-clone-manifest-v2',
        'package_name': spec.get('package_name', 'Zaskaleta MASTER CLONE'),
        'version': args.version,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'scene_independent': True,
        'canonical_identity_photo': canonical.name if canonical else None,
        'master_voice': voice.name if voice and voice.is_file() else None,
        'identity_reference_count': len(copied['identity']),
        'motion_reference_count': len(copied['motion']),
        'primary_behavior': Path(assets['primary_behavior']).name if assets.get('primary_behavior') else None,
        'identity_lock': profile.get('realism_lock', {}).get('identity', []),
        'motion_lock': profile.get('realism_lock', {}).get('motion', []),
        'talking_engine': spec['components']['talking']['engine'],
        'voice_pipeline': {
            'base_tts': spec['components']['voice']['base_tts'],
            'voice_conversion': spec['components']['voice']['voice_conversion'],
            'language': spec['components']['voice']['language']
        },
        'approved_policy': spec['reference_policy']['approved'],
        'rejected_policy': spec['reference_policy']['rejected'],
        'test_gates': spec['test_gates'],
        'reference_integrity': {
            'algorithm': 'sha256',
            'identity': [file_record(p, package) for p in copied['identity']],
            'motion': [file_record(p, package) for p in copied['motion']],
            'voice': file_record(copied['voice'], package) if copied['voice'] else None,
        },
        'approval_state': {
            'automatic_master_promotion': False,
            'manual_review_required': True,
        },
    }

    manifest_path = package / 'MASTER_CLONE_MANIFEST.json'
    atomic_write_json(manifest_path, package_manifest)
    atomic_write_json(versions_dir / 'manifest.json', package_manifest)

    integrity_payload = {
        'schema': 'zaskaleta-master-clone-integrity-v1',
        'version': args.version,
        'created_at': package_manifest['created_at'],
        'manifest_sha256': sha256_file(manifest_path),
        'references': package_manifest['reference_integrity'],
    }
    atomic_write_json(versions_dir / 'integrity.json', integrity_payload)

    (package / 'APPROVED' / 'README.txt').write_text(
        'Only real photos, real videos, real voice, and manually approved generations belong here.\n',
        encoding='utf-8'
    )
    (package / 'REJECTED' / 'README.txt').write_text(
        'Put face drift, wrong beard/age/mouth/jaw, unnatural motion, and wrong identity results here.\n',
        encoding='utf-8'
    )
    atomic_write_json(package / 'TALKING' / 'PROFILE.json', spec['components']['talking'])
    atomic_write_json(package / 'MOTION' / 'PROFILE.json', spec['components']['motion'])
    atomic_write_json(package / 'VOICE' / 'PROFILE.json', spec['components']['voice'])
    atomic_write_json(package / 'IDENTITY' / 'PROFILE.json', spec['components']['identity'])

    print('✅ MASTER CLONE PACKAGE READY')
    print('ROOT=' + str(package))
    print('VERSION=' + args.version)
    print('IDENTITY_REFERENCES=' + str(len(copied['identity'])))
    print('MOTION_REFERENCES=' + str(len(copied['motion'])))
    print('MASTER_VOICE=' + str(bool(copied['voice'])))
    print('MANIFEST_SHA256=' + integrity_payload['manifest_sha256'])


if __name__ == '__main__':
    main()
