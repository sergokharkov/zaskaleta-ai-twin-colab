#!/usr/bin/env python3
import argparse, hashlib, json, subprocess
from pathlib import Path

EXPECTED = {
    '53665.jpg': ('image', None),
    '53666.jpg': ('image', None),
    '53668.jpg': ('image', None),
    '53669.jpg': ('image', None),
    '54193.mp4': ('video', (15.0, 17.0)),
    '54194.mp4': ('video', (35.0, 38.0)),
    '54197.mp4': ('video', (117.0, 121.0)),
}

def sha256(p: Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024), b''): h.update(c)
    return h.hexdigest()

def probe_video(p: Path):
    raw=subprocess.check_output([
        'ffprobe','-v','error','-select_streams','v:0',
        '-count_frames','-show_entries','stream=duration,width,height,avg_frame_rate,nb_read_frames',
        '-of','json',str(p)
    ], text=True)
    d=json.loads(raw)['streams'][0]
    dur=float(d['duration'])
    frames=int(d.get('nb_read_frames') or 0)
    if int(d['width']) != 1920 or int(d['height']) != 1080:
        raise RuntimeError(f'{p.name}: unexpected dimensions')
    if frames <= 0:
        raise RuntimeError(f'{p.name}: no readable video frames')
    return {'duration':dur,'frames':frames,'avg_frame_rate':d.get('avg_frame_rate')}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--pass-id', required=True)
    ap.add_argument('--source-sha', required=True)
    ap.add_argument('--output', required=True)
    a=ap.parse_args()
    root=Path(a.root)
    manifest=root/'private_manifest.json'
    if not manifest.is_file(): raise SystemExit('private_manifest.json missing')
    m=json.loads(manifest.read_text())
    if m.get('candidate_id')!='MASTER_CLONE_CANDIDATE_005': raise SystemExit('wrong candidate')
    assets={x['filename']:x for x in (m.get('assets') or [])}
    if set(assets)!=set(EXPECTED): raise SystemExit('private asset set mismatch')
    details={}
    for name,(kind,drange) in EXPECTED.items():
        p=root/name
        if not p.is_file(): raise SystemExit(f'{name} missing')
        meta=assets[name]
        if p.stat().st_size != int(meta['size_bytes']): raise SystemExit(f'{name} size mismatch')
        if sha256(p) != meta['sha256']: raise SystemExit(f'{name} sha mismatch')
        if kind=='video':
            q=probe_video(p)
            if not (drange[0] <= q['duration'] <= drange[1]): raise SystemExit(f'{name} duration out of range')
            details[name]={'duration':round(q['duration'],6),'frames':q['frames']}
        else:
            raw=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',str(p)],text=True)
            d=json.loads(raw)['streams'][0]
            if int(d['width']) <= 0 or int(d['height']) <= 0: raise SystemExit(f'{name} invalid image')
            details[name]={'width':int(d['width']),'height':int(d['height'])}
    out={
        'schema':'zaskaleta-c005-private-cpu-qa-v1',
        'candidate_id':'MASTER_CLONE_CANDIDATE_005',
        'source_sha':a.source_sha,
        'pass_id':a.pass_id,
        'decision':'CPU_QA_PASS',
        'complete':True,
        'private_asset_count':len(EXPECTED),
        'gpu_launch_allowed':False,
        'stable_release_modified':False,
        'details':details,
    }
    Path(a.output).write_text(json.dumps(out,indent=2)+'\n')
    print('C005_PRIVATE_CPU_QA_PASS='+a.pass_id)
if __name__=='__main__': main()
