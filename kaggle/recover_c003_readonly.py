#!/usr/bin/env python3
"""Run pinned C003 recovery with writable, run-scoped package extraction."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import recover_c003 as recovery

original_build = recovery.build


def build(out, source, token, handle, dataset):
    manifest = original_build(out, source, token, handle, dataset)
    code = out / 'c003_runner.py'
    text = code.read_text()
    old = "here = Path(__file__).resolve().parent\n"
    new = "here = Path(os.environ.get('ZASKALETA_LAUNCH_WORK', '/kaggle/working')) / 'c003-package-%s'\nhere.mkdir(parents=True, exist_ok=True)\n" % token
    if text.count(old) != 1:
        raise RuntimeError('Unexpected launcher source; refusing an unverified patch')
    text = text.replace(old, new, 1)
    code.write_text(text)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        src, work = base / 'src', base / 'working'
        src.mkdir(); work.mkdir()
        submitted = src / 'script.py'
        submitted.write_text(text)
        src.chmod(0o555); submitted.chmod(0o444)
        try:
            env = {**os.environ, 'ZASKALETA_PACKAGING_TEST': '1',
                   'ZASKALETA_LAUNCH_WORK': str(work)}
            kwargs = {}
            if os.geteuid() == 0:
                nobody = pwd.getpwnam('nobody')
                base.chmod(0o755); work.chmod(0o777)
                def drop_privileges():
                    os.setgroups([])
                    os.setgid(nobody.pw_gid)
                    os.setuid(nobody.pw_uid)
                kwargs['preexec_fn'] = drop_privileges
            subprocess.run([sys.executable, str(submitted)], cwd=str(work),
                           env=env, check=True, timeout=60, **kwargs)
            expected = work / ('c003-package-' + token)
            for name in ('source_bundle.zip', 'run_identity.json', 'verified_c003_entry.py'):
                if not (expected / name).is_file():
                    raise RuntimeError('Read-only packaging test missing ' + name)
        finally:
            src.chmod(0o755)
    print('C003_READONLY_PACKAGE_TEST_OK', token, flush=True)
    return manifest


recovery.build = build

if __name__ == '__main__':
    raise SystemExit(recovery.main())
