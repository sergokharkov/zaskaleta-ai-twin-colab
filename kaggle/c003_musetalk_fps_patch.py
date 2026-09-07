#!/usr/bin/env python3
"""Patch C003 to accept only MuseTalk FPS drift within the approved reference tolerance."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError(f'expected exactly one match in {path}')
    text = text.replace(old, new, 1)
    ast.parse(text)
    p.write_text(text, encoding='utf-8')


def apply():
    renderer = 'kaggle/first_gate_alignment_render.py'
    replace(renderer,
"""def validate_final(meta, audio_duration, final_sr, fps, intermediate):
""",
"""def enforce_render_fps(meta, approved_fps, tolerance):
    render_fps = Fraction(video_stream(meta)['avg_frame_rate'])
    if render_fps <= 0 or abs(render_fps - approved_fps) > tolerance:
        raise RuntimeError(f'MuseTalk FPS {render_fps} differs from approved reference {approved_fps} beyond tolerance {tolerance}')
    return render_fps

def validate_final(meta, audio_duration, final_sr, fps, intermediate):
""")
    replace(renderer,
"""    fps = enforce_reference_policy(motion_meta, target, align)
""",
"""    fps = enforce_reference_policy(motion_meta, target, align)
    fps_tolerance = Fraction(str(align.get('reference_fps_tolerance', 0)))
""")
    replace(renderer,
"""    intermediate_meta = probe(musetalk_render)
    if Fraction(video_stream(intermediate_meta)['avg_frame_rate']) != fps:
        raise RuntimeError('MuseTalk changed approved reference FPS')
    if duration_seconds(intermediate_meta) + 0.04 < final_audio_duration:
""",
"""    intermediate_meta = probe(musetalk_render)
    render_fps = enforce_render_fps(intermediate_meta, fps, fps_tolerance)
    if duration_seconds(intermediate_meta) + 0.04 < final_audio_duration:
""")
    replace(renderer,
"""    render_duration = validate_final(render_meta, final_audio_duration, final_sr, fps, intermediate_meta)
""",
"""    render_duration = validate_final(render_meta, final_audio_duration, final_sr, render_fps, intermediate_meta)
""")
    replace(renderer,
"""        'reference_fps':round(float(fps),6),'reference_fps_rational':str(fps),'render_duration_seconds':round(render_duration,3),
""",
"""        'reference_fps':round(float(fps),6),'reference_fps_rational':str(fps),
        'render_fps':round(float(render_fps),6),'render_fps_rational':str(render_fps),
        'reference_fps_tolerance':float(fps_tolerance),'render_duration_seconds':round(render_duration,3),
""")
    replace(renderer,
"""        'reference_fps':round(float(fps),6),'reference_fps_rational':str(fps),'do_not_repeat_reference_motion':True,
""",
"""        'reference_fps':round(float(fps),6),'reference_fps_rational':str(fps),
        'render_fps':round(float(render_fps),6),'render_fps_rational':str(render_fps),
        'reference_fps_tolerance':float(fps_tolerance),'do_not_repeat_reference_motion':True,
""")

    test = 'kaggle/test_c003_release.py'
    replace(test,
"""        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(meta, 10.0, {**policy, 'do_not_repeat_reference_motion': False})

    def test_final_audio_and_video_timing(self):
""",
"""        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(meta, 10.0, {**policy, 'do_not_repeat_reference_motion': False})
        self.assertEqual(renderer.enforce_render_fps(
            {'streams':[{'codec_type':'video','avg_frame_rate':'30/1'}]}, Fraction(exact_fps), Fraction('0.1')), Fraction(30,1))
        with self.assertRaises(RuntimeError):
            renderer.enforce_render_fps(
                {'streams':[{'codec_type':'video','avg_frame_rate':'25/1'}]}, Fraction(exact_fps), Fraction('0.1'))

    def test_final_audio_and_video_timing(self):
""")
    print('C003_MUSETALK_FPS_TOLERANCE_PATCH_APPLIED')


def verify():
    renderer = (ROOT/'kaggle/first_gate_alignment_render.py').read_text(encoding='utf-8')
    test = (ROOT/'kaggle/test_c003_release.py').read_text(encoding='utf-8')
    ast.parse(renderer); ast.parse(test)
    assert 'def enforce_render_fps' in renderer
    assert 'render_fps = enforce_render_fps' in renderer
    assert 'reference_fps_tolerance' in renderer
    assert 'renderer.enforce_render_fps' in test
    print('C003_MUSETALK_FPS_TOLERANCE_PATCH_VERIFIED')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()
    if args.apply: apply()
    if args.verify: verify()
