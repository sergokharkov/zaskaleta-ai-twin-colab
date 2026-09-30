#!/usr/bin/env python3
"""Execute C006 from an immutable run-scoped source bundle."""
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

WORK = Path("/kaggle/working")
REPO = WORK / "zaskaleta-ai-twin-colab"
EXPECTED = "MASTER_CLONE_CANDIDATE_006"
STATUS = WORK / "zaskaleta_c006_status.json"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    here = Path(__file__).resolve().parent
    identity = json.loads((here / "run_identity.json").read_text(encoding="utf-8"))
    token = identity["run_token"]
    source_sha = identity["source_sha"]

    if identity.get("candidate_id") != EXPECTED or not token:
        raise RuntimeError("invalid C006 run identity")
    if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise RuntimeError("invalid source SHA")

    bundle = here / "source_bundle.zip"
    if digest(bundle) != identity["bundle_sha256"]:
        raise RuntimeError("C006 source bundle digest mismatch")

    if REPO.exists():
        raise RuntimeError("refusing to overwrite existing C006 workspace")
    REPO.mkdir(parents=True)

    with zipfile.ZipFile(bundle) as zf:
        for member in zf.infolist():
            p = Path(member.filename)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError("unsafe source bundle path")
        zf.extractall(REPO)

    for name, expected in identity["critical_source_hashes"].items():
        path = REPO / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError("pinned C006 source mismatch: " + name)

    spec = importlib.util.spec_from_file_location(
        "c006_kernel", REPO / "kaggle" / "autopilot_kernel_c006.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    original_status = module.write_status

    def write_status(**kwargs):
        kwargs.update(
            run_token=token,
            source_sha=source_sha,
            expected_candidate_id=EXPECTED,
        )
        original_status(**kwargs)

    module.write_status = write_status
    rc = module.main()
    if rc != 0:
        return rc

    status = json.loads(STATUS.read_text(encoding="utf-8"))
    if status.get("candidate_id") != EXPECTED:
        raise RuntimeError("C006 terminal candidate mismatch")
    if status.get("state") != "C006_RENDER_READY_FOR_MANUAL_REVIEW":
        raise RuntimeError("C006 terminal status mismatch")
    if status.get("render_completed") is not True:
        raise RuntimeError("C006 render not completed")
    if status.get("promotion_allowed") is not False:
        raise RuntimeError("C006 promotion unexpectedly allowed")
    if status.get("auto_promote") is not False:
        raise RuntimeError("C006 auto-promotion safety weakened")
    if status.get("stable_release_modified") is not False:
        raise RuntimeError("C006 stable release was modified")
    if status.get("subjective_identity_review") != "PENDING_MANUAL_REVIEW":
        raise RuntimeError("C006 bypassed manual identity review")
    if status.get("raw_private_media_exported_to_github") is not False:
        raise RuntimeError("C006 private media export policy violated")
    if status.get("private_hashes_exported") is not False:
        raise RuntimeError("C006 private hash export policy violated")
    if status.get("face_embeddings_exported") is not False:
        raise RuntimeError("C006 embedding export policy violated")

    qa = status.get("stream_qa") or {}
    if float(qa.get("video_duration") or 0) < 7.5:
        raise RuntimeError("C006 output stream QA missing/invalid")

    print("VERIFIED_C006_RENDER_READY_FOR_MANUAL_REVIEW", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("FAILED_CLOSED: " + repr(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
