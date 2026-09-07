#!/usr/bin/env python3
"""Remediate the C003 reference-FPS contract while preserving exact approved motion timing."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def replace(rel, old, new):
    path = ROOT / rel
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError(f'Expected unique contract not found in {rel}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


def main():
    profile_path = ROOT / 'content/talking_profile_v2.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    align = profile['audio_alignment']
    if align.get('preserve_reference_fps') != 25:
        raise RuntimeError('Unexpected old nominal FPS contract')
    align['preserve_reference_fps'] = 30
    align['reference_fps_tolerance'] = 0.1
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    renderer = 'kaggle/first_gate_alignment_render.py'
    replace(renderer,
"""    preserve_reference_fps = int(align['preserve_reference_fps'])
    do_not_repeat_reference_motion = align['do_not_repeat_reference_motion'] is True
    if not do_not_repeat_reference_motion or align.get('do_not_loop_audio') is not True:
        raise RuntimeError('Reference/audio loop policy weakened')
    fps = Fraction(video_stream(meta)['avg_frame_rate'])
    if fps != preserve_reference_fps:
        raise RuntimeError(f'Approved reference FPS {fps} does not match required {preserve_reference_fps}; normalization requires a separate approved change')
    if duration_seconds(meta) + 0.001 < target:
        raise RuntimeError('Approved motion reference is shorter than speech; repetition is forbidden')
    return preserve_reference_fps
""",
"""    nominal_fps = Fraction(str(align['preserve_reference_fps']))
    tolerance = Fraction(str(align.get('reference_fps_tolerance', 0)))
    do_not_repeat_reference_motion = align['do_not_repeat_reference_motion'] is True
    if not do_not_repeat_reference_motion or align.get('do_not_loop_audio') is not True:
        raise RuntimeError('Reference/audio loop policy weakened')
    fps = Fraction(video_stream(meta)['avg_frame_rate'])
    if fps <= 0 or abs(fps - nominal_fps) > tolerance:
        raise RuntimeError(f'Approved reference FPS {fps} is outside nominal {nominal_fps} +/- {tolerance}; source timing must not be normalized implicitly')
    if duration_seconds(meta) + 0.001 < target:
        raise RuntimeError('Approved motion reference is shorter than speech; repetition is forbidden')
    return fps
""")
    replace(renderer,
            "'reference_fps':int(fps),'render_duration_seconds'",
            "'reference_fps':round(float(fps),6),'reference_fps_rational':str(fps),'render_duration_seconds'")
    replace(renderer,
            "'reference_fps':int(fps),'do_not_repeat_reference_motion'",
            "'reference_fps':round(float(fps),6),'reference_fps_rational':str(fps),'do_not_repeat_reference_motion'")

    tests = 'kaggle/test_c003_release.py'
    replace(tests,
"""        meta = {'format': {'duration': '12.0'}, 'streams': [{'codec_type': 'video', 'avg_frame_rate': '25/1'}]}
        policy = {'preserve_reference_fps': 25, 'do_not_repeat_reference_motion': True, 'do_not_loop_audio': True}
        self.assertEqual(renderer.enforce_reference_policy(meta, 10.0, policy), 25)
        bad = copy.deepcopy(meta)
        bad['streams'][0]['avg_frame_rate'] = '30/1'
        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(bad, 10.0, policy)
""",
"""        exact_fps = '24660000/821821'
        meta = {'format': {'duration': '12.0'}, 'streams': [{'codec_type': 'video', 'avg_frame_rate': exact_fps}]}
        policy = {'preserve_reference_fps': 30, 'reference_fps_tolerance': 0.1, 'do_not_repeat_reference_motion': True, 'do_not_loop_audio': True}
        self.assertEqual(renderer.enforce_reference_policy(meta, 10.0, policy), Fraction(exact_fps))
        bad = copy.deepcopy(meta)
        bad['streams'][0]['avg_frame_rate'] = '25/1'
        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(bad, 10.0, policy)
""")
    replace(tests, 'from fractions import Fraction\n' if 'from fractions import Fraction\n' in (ROOT/tests).read_text() else 'import copy\n', 'import copy\nfrom fractions import Fraction\n' if 'from fractions import Fraction\n' not in (ROOT/tests).read_text() else 'from fractions import Fraction\n')

    audit = 'kaggle/audit_c003_source.py'
    replace(audit,
            "align['do_not_repeat_reference_motion'] is True and align['preserve_reference_fps']==25)",
            "align['do_not_repeat_reference_motion'] is True and align['preserve_reference_fps']==30 and align.get('reference_fps_tolerance')==0.1)")

    safety = 'kaggle/c003_release_safety.py'
    replace(safety,
            "assert \"'reference_fps':int(fps)\" in renderer",
            "assert \"'reference_fps':round(float(fps),6)\" in renderer\n    assert \"'reference_fps_rational':str(fps)\" in renderer")

    print('C003_REFERENCE_FPS_REMEDIATION_APPLIED')


if __name__ == '__main__':
    main()
