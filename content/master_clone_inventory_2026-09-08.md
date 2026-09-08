# Zaskaleta MASTER CLONE — verified inventory

Date: 2026-09-08

## Scope

This registry records only assets and locations that were actually verified from the current GitHub `main` tree or connected Google Drive metadata. It does not claim the existence of any private Kaggle file or trained checkpoint unless that file was directly verified.

## GitHub control plane

Repository: `sergokharkov/zaskaleta-ai-twin-colab`

Current inspected main SHA before this inventory commit: `15fc4cb5516380cbbc81328b87824aab92c3bffe`

Core package/configuration files verified in the repository:

- `content/master_clone_package.json`
- `content/clone_reference_profile.json`
- `content/c004_source_manifest_v1.json`
- `content/clone_quality_gate_v1.json`
- `content/identity_view_holdout_v1.json`
- `content/motion_learning_profile_v1.json`
- `content/motion_profile.json`
- `content/talking_profile_v2.json`
- `content/talking_temporal_guard_v1.json`
- `content/render_face_guard_v1.json`
- `content/clone_memory_checkpoint_policy_v1.json`
- `content/clone_memory_policy_v1.json`
- `content/clone_release_policy_v1.json`
- `content/clone_release_ledger_v1.json`
- `content/clone_promotion_bundle_policy_v1.json`
- `content/clone_speech_lineage_policy_v1.json`
- `content/storage_config.json`
- `content/training_candidates_2026-09-05.json`
- `content/user_video_candidates_20260905.json`

Core runtime/validation code verified in the repository:

- `worker/run_clone_v2_test.py`
- `worker/run_c004_authorized.py`
- `worker/validate_c004_source.py`
- `worker/c004_runtime_guard.py`
- `worker/lipsync_musetalk.py`
- `worker/voice_mms_openvoice.py`
- `worker/locate_clone_assets.py`
- `worker/validate_clone_v2_temporal_output.py`
- `worker/validate_master_clone.py`
- `worker/build_master_clone_package.py`
- `kaggle/autopilot_kernel_c004.py`
- `kaggle/verified_c004_entry.py`
- `kaggle/bootstrap.py`
- `kaggle/prepare_models.py`

## Identity reference definition

The repository package defines:

- Canonical identity anchor: `MVIMG_20260830_144834.jpg`
- Supporting identity references: 5 additional photos
- Total master photo count: 6
- Identity policy: preserve exact facial structure, natural asymmetry, eye shape, nose, lips, jaw, beard, hairstyle/hairline, age and skin tone; no beautification/rejuvenation/face reshaping/lookalike substitution.

The six filenames recorded in `content/clone_reference_profile.json` are:

1. `MVIMG_20260830_144834.jpg`
2. `MVIMG_20260830_144843.jpg`
3. `MVIMG_20260830_144606.jpg`
4. `MVIMG_20260830_144457.jpg`
5. `image-1788277947699.jpg`
6. `image-1788277957517.jpg`

These filenames are verified as configuration references. Their current physical storage bytes were not re-verified during this inventory.

## Voice definition

The repository package defines:

- Master voice filename: `Zaskaleta_AI_Voice_Master.mp3`
- Language: Ukrainian (`uk`)
- Base TTS: `facebook/mms-tts-ukr`
- Voice conversion: OpenVoice V2

The filename and engine configuration are verified in GitHub configuration. The current physical master-audio file bytes were not re-verified during this inventory.

## Talking / lip-sync stack

Verified configured engine:

- MuseTalk 1.5

Verified supporting runtime code:

- `worker/lipsync_musetalk.py`
- `worker/run_clone_v2_test.py`
- `worker/validate_lipsync_render_provenance.py`
- `content/talking_profile_v2.json`
- `content/talking_temporal_guard_v1.json`

## Motion / behavior definition

The package defines MMPose 1.1.0 as the motion-learning engine and records the following intended motion references:

- `MASTER_BEHAVIOR_01.mp4`
- `MASTER_BEHAVIOR_02.mp4`

`content/clone_reference_profile.json` also records these behavior-video source names:

- `VID_20260901_175254.mp4` — 15.996 s, verified in profile metadata
- `VID_20260901_175350.mp4` — 36.525 s, verified in profile metadata
- `VID_20260901_183436.mp4` — long-form behavior reference, profile marks direct verification false
- `VID_20260901_184333.mp4` — 131.219 s, profile marks verified
- `VID_20260901_184558.mp4` — 119.135 s, profile marks verified
- `VID_20260901_184810.mp4` — 86.286 s, profile marks verified

These are configuration/profile records. This inventory does not claim that every listed file is currently present in the same Drive folder.

## Google Drive locations actually verified

Connected Drive search verified:

- Folder `Zaskaleta MASTER CLONE`
  - Drive folder id: `1nK6d3YvOKSkS8acDgwXxItthVadERQKf`
  - Direct child verified: folder `C004`
  - Current search returned no direct contents inside `C004`; treat it as empty/unresolved until additional files are confirmed.

- Folder referenced by `content/clone_reference_profile.json`
  - Drive folder id: `13Wye5lVZPOcUryXbXhplad_FkxM7lG4a`
  - Direct children currently verified by Drive metadata:
    - `clone_v1_tests`
    - `episodes`
    - `Довідка Засклета.pdf`

- `clone_v1_tests`
  - Drive folder id: `1QhvNyNBqH8bCBCZl5kTP4x673gZxYqRg`
  - Direct child currently verified: folder `speech`

No claim is made that these folder listings are exhaustive beyond the metadata returned by the connected Drive search at inventory time.

## C004 state

Verified output candidate:

- Candidate id: `MASTER_CLONE_GATE_08_15_CANDIDATE_004`
- Existing GPU render source SHA: `5f904215c1c85b4908ed633486f8f408c8062ad5`
- Recovered MP4 filename: `MASTER_CLONE_GATE_08_15_CANDIDATE_004.mp4`
- Size: 1,209,983 bytes
- Duration: 8.0 s
- Resolution: 1080x1920
- Video codec: H.264
- Audio codec: AAC
- Manual identity review: not approved as stable
- Stable release: unchanged

## Trained checkpoint status

No standalone personalized neural-network checkpoint file was found in the current GitHub tree. The repository contains checkpoint policy/validation infrastructure (`clone_memory_checkpoint_policy_v1.json`, `validate_clone_memory_checkpoint.py`, QA workflow), but that is not evidence of a trained personal model checkpoint.

Therefore current status is:

- Personal identity reference package: DEFINED
- Voice reference definition: DEFINED
- Motion/behavior reference definition: DEFINED
- Talking/lip-sync runtime: DEFINED
- C004 rendered candidate: VERIFIED
- Stable MASTER release from C004: NOT APPROVED
- Standalone trained personal checkpoint: NOT VERIFIED

## New current-conversation material not yet imported into the persistent clone base

The following new files were supplied in the current ChatGPT conversation and analyzed locally, but have not been added to Drive/Kaggle/reference manifests by this inventory action:

Photos:
- `53666.jpg`
- `53665.jpg`
- `53668.jpg`
- `53669.jpg`

Videos:
- `54193.mp4` — 15.996 s, 1920x1080, 30 fps
- `54194.mp4` — 36.525 s, 1920x1080, 30 fps
- `54197.mp4` — 119.135 s, 1920x1080, HEVC

These should remain candidate reference material until explicitly curated and imported into the private clone data store.

## Next controlled action

1. Curate the new photos/videos for identity, articulation and behavior roles.
2. Upload approved originals only to the private data store.
3. Update the reference manifest without replacing the current stable release.
4. Build C005 as a challenger only after identity-preservation QA passes.
