import argparse
import json
from pathlib import Path


def fail(msg):
    raise SystemExit('❌ ' + msg)


def main():
    ap = argparse.ArgumentParser(description='Validate Zaskaleta MASTER CLONE package integrity before testing')
    ap.add_argument('--package-root', required=True)
    args = ap.parse_args()

    root = Path(args.package_root)
    manifest_path = root / 'MASTER_CLONE_MANIFEST.json'
    if not manifest_path.is_file():
        fail('MASTER_CLONE_MANIFEST.json missing')

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    required_dirs = ['IDENTITY', 'MOTION', 'TALKING', 'VOICE', 'APPROVED', 'REJECTED', 'VERSIONS']
    missing_dirs = [d for d in required_dirs if not (root / d).is_dir()]
    if missing_dirs:
        fail('Missing directories: ' + ', '.join(missing_dirs))

    identity_files = [p for p in (root / 'IDENTITY').iterdir() if p.is_file() and p.name != 'PROFILE.json']
    motion_files = [p for p in (root / 'MOTION').iterdir() if p.is_file() and p.name != 'PROFILE.json']
    voice_files = [p for p in (root / 'VOICE').iterdir() if p.is_file() and p.name != 'PROFILE.json']

    if len(identity_files) < 6:
        fail(f'Identity references incomplete: {len(identity_files)}/6')
    if not any(p.name.startswith('CANONICAL_') for p in identity_files):
        fail('Canonical identity anchor missing')
    if len(motion_files) < 2:
        fail(f'Motion references incomplete: {len(motion_files)}/2')
    if not voice_files:
        fail('Master voice missing')
    if not manifest.get('scene_independent'):
        fail('Package is not marked scene-independent')

    print('✅ MASTER CLONE PACKAGE VALID')
    print('VERSION=' + str(manifest.get('version')))
    print('IDENTITY_REFERENCES=' + str(len(identity_files)))
    print('MOTION_REFERENCES=' + str(len(motion_files)))
    print('VOICE_REFERENCES=' + str(len(voice_files)))
    print('CANONICAL=' + str(manifest.get('canonical_identity_photo')))
    print('PRIMARY_BEHAVIOR=' + str(manifest.get('primary_behavior')))
    print('NEXT_GATE=' + str((manifest.get('test_gates') or [{}])[0].get('stage', '8-15s')))


if __name__ == '__main__':
    main()
