#!/usr/bin/env python3
"""Source-only C003 audit. No project imports, GPU, network, or private data."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_003'
issues = []
checks = []
counts = {'tracked_files':0,'python_files':0,'json_files':0,'notebooks':0,'workflow_files':0}

def check(name, ok, detail=''):
    checks.append({'name':name,'passed':bool(ok),'detail':detail})
    if not ok: issues.append({'check':name,'severity':'error','detail':detail})

def source(path): return (ROOT/path).read_text(encoding='utf-8-sig')
def parse_json(path): return json.loads(source(path))
def tree(path): return ast.parse(source(path), filename=path)

def assignments(path):
    result={}
    for node in ast.walk(tree(path)):
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name):
                    try: result[target.id]=ast.literal_eval(node.value)
                    except (ValueError,TypeError,SyntaxError,RecursionError): pass
    return result

def functions(path):
    return {n.name for n in ast.walk(tree(path)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

def main():
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    paths=[Path(p.decode()) for p in subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).split(b'\0') if p]
    all_files={p.as_posix() for p in paths}
    counts['tracked_files']=len(paths)
    for path in paths:
        rel=path.as_posix()
        try:
            if path.suffix not in ('.py','.json','.ipynb','.yml','.yaml'): continue
            text=source(rel)
            if path.suffix=='.py':
                compile(text,rel,'exec'); counts['python_files']+=1
            elif path.suffix=='.json':
                json.loads(text); counts['json_files']+=1
            elif path.suffix=='.ipynb':
                notebook=json.loads(text)
                if notebook.get('nbformat',0)<4: raise RuntimeError('Unsupported notebook format')
                for i,cell in enumerate(notebook.get('cells',[])):
                    if cell.get('cell_type')!='code': continue
                    code=''.join(cell.get('source',[]))
                    if not any(line.lstrip().startswith(('%','!')) for line in code.splitlines()): compile(code,rel+':cell:'+str(i),'exec')
                counts['notebooks']+=1
            else:
                import yaml
                yaml.load(text,Loader=yaml.BaseLoader)
                counts['workflow_files']+=int(rel.startswith('.github/workflows/'))
        except Exception as exc:
            check('file:'+rel,False,type(exc).__name__+': '+str(exc)[:250])
    check('repository_syntax',not any(x['check'].startswith('file:') for x in issues),'All tracked source formats parsed')
    required=[
        'kaggle/recover_c003.py','kaggle/recover_c003_readonly.py','kaggle/verified_c003_entry.py',
        'kaggle/autopilot_kernel_c003.py','kaggle/first_gate_alignment_render.py','kaggle/auto_prepare.py',
        'kaggle/bootstrap.py','kaggle/prepare_models.py','kaggle/preflight.py','kaggle/validate_private_assets.py',
        'worker/lipsync_musetalk.py','worker/voice_mms_openvoice.py','worker/generate_scene_speech.py',
        'worker/evaluate_clone_release.py','content/talking_profile_v2.json','content/clone_reference_profile.json',
        'content/master_clone_package.json','content/clone_duration_gate_policy_v1.json',
        'content/clone_quality_gate_v1.json','content/clone_release_policy_v1.json','content/identity_view_holdout_v1.json']
    check('complete_c003_dependency_files',set(required).issubset(all_files),', '.join(sorted(set(required)-all_files)))
    pinned=assignments('kaggle/recover_c003.py').get('SOURCE_FILES',[])
    check('pinned_source_file_list',set(required).issubset(set(pinned)),'Missing: '+', '.join(sorted(set(required)-set(pinned))))
    profile=parse_json('content/clone_reference_profile.json')
    package=parse_json('content/master_clone_package.json')
    talking=parse_json('content/talking_profile_v2.json')
    duration=parse_json('content/clone_duration_gate_policy_v1.json')
    quality=parse_json('content/clone_quality_gate_v1.json')
    release=parse_json('content/clone_release_policy_v1.json')
    check('identity_reference_contract',profile['canonical_identity_photo'] in profile['photo_filenames'] and len(profile['photo_filenames'])==profile['master_photo_count']==6 and package['components']['identity']['canonical_anchor']==profile['canonical_identity_photo'])
    check('motion_reference_contract',package['components']['motion']['supporting_reference']==talking['reference_policy']['supporting']=='MASTER_BEHAVIOR_02.mp4')
    align=talking['audio_alignment']
    check('audio_policy',align['lipsync_sample_rate']==16000 and align['final_audio_sample_rate']==24000 and align['pad_end_only'] is True and align['do_not_loop_audio'] is True and align['do_not_repeat_reference_motion'] is True and align['preserve_reference_fps']==25)
    check('duration_policy',duration['ordered_gates'][0]=={'id':'gate_08_15','min_seconds':8,'max_seconds':15} and duration['rules']['manual_promotion_required'] is True and duration['rules']['identity_regression_tolerance']==0)
    check('promotion_policy',quality['manual_approval_required'] is True and quality['auto_promote_to_master'] is False and release['auto_promote'] is False and release['identity_regression_tolerance']==0)
    renderer=source('kaggle/first_gate_alignment_render.py')
    adapter=source('worker/lipsync_musetalk.py')
    voice=source('worker/voice_mms_openvoice.py')
    check('exact_candidate_contract',CANDIDATE in renderer and CANDIDATE in source('kaggle/verified_c003_entry.py'))
    check('read_only_launcher',"'/kaggle/working'" in source('kaggle/recover_c003_readonly.py') and 'C003_READONLY_PACKAGE_TEST_OK' in source('kaggle/recover_c003_readonly.py'))
    check('no_implicit_photo_fallback','if args.allow_photo_fallback:' in adapter and "('approved-animated-reference', reference)" in adapter)
    check('no_unapproved_motion_learning',package['components']['motion']['learning_policy']['auto_training'] is False and package['components']['motion']['learning_policy']['manual_approval_required'] is True)
    check('voice_converter_import','ToneColorConverter' in voice and 'facebook/mms-tts-ukr' in voice)
    check('status_schema_complete','write_status' in functions('kaggle/autopilot_kernel_c003.py') and 'payload.update' in source('kaggle/autopilot_kernel_c003.py') and 'promotion_allowed=False' in source('kaggle/autopilot_kernel_c003.py'),'Behavioral override regression test must also pass')
    check('run_identity_enforced',{'safe_archive_members','main','digest'}.issubset(functions('kaggle/verified_c003_entry.py')) and 'source_hashes' in source('kaggle/verified_c003_entry.py'))
    check('output_is_run_scoped',"'c003-package-" in source('kaggle/recover_c003_readonly.py') and 'c003-source-' in source('kaggle/verified_c003_entry.py'))
    check('no_audio_truncation','raw_duration > 15.0' in renderer and 'apad=pad_dur=' in renderer and 'validate_final' in functions('kaggle/first_gate_alignment_render.py'))
    check('reference_fps_policy_enforced',{'enforce_reference_policy','validate_final'}.issubset(functions('kaggle/first_gate_alignment_render.py')) and 'do_not_repeat_reference_motion' in renderer)
    check('provenance_final_mp4','final_render_sha256' in renderer and 'intermediate_render_sha256' in renderer and 'provenance_sha256' in renderer)
    check('worker_exit_failure','if result.returncode == 0 and rendered is not None:' in adapter)
    gpu_workflows=[]
    for p in paths:
        rel=p.as_posix()
        if not rel.startswith('.github/workflows/') or p.suffix not in ('.yml','.yaml'): continue
        text=source(rel)
        if 'kaggle kernels push' in text or "'kernels', 'push'" in text or "'kernels','push'" in text:
            gpu_workflows.append(rel)
            import yaml
            workflow=yaml.load(text,Loader=yaml.BaseLoader)
            check('no_auto_gpu:'+rel,'push' not in workflow.get('on',{}),'GPU submission must not trigger on push')
    checks.append({'name':'gpu_workflow_inventory','passed':True,'detail':', '.join(gpu_workflows)})
    active=source('.github/workflows/kaggle-c003-readonly.yml')
    check('explicit_gpu_release_gate','ZASKALETA_C003_GPU_APPROVED' in active)
    check('bounded_gpu_job','timeout-minutes:' in active and 'if-no-files-found: error' in active)
    check('safe_export_only','.kaggle-review/MASTER_CLONE_GATE_08_15_CANDIDATE_003.mp4' in active and 'MASTER_BEHAVIOR_' not in active and 'Zaskaleta_AI_Voice_Master.mp3' not in active)
    banned=re.compile(r'(^|/)(?:MVIMG_20260830_\d+\.jpg|image-17882779\d+\.jpg|Zaskaleta_AI_Voice_Master\.(?:mp3|wav)|MASTER_BEHAVIOR_\d+\.mp4|\.env(?:\..*)?|kaggle\.json)$')
    check('private_media_not_tracked',not any(banned.search(p) for p in all_files),'Private source filename detected' if any(banned.search(p) for p in all_files) else '')
    check('private_dataset_not_accessed',True,'Source-only; no private Kaggle access')
    report={'schema':'zaskaleta-c003-source-audit-v1','source_sha':commit,'source_only':True,'gpu_started':False,'private_assets_accessed':False,'stable_release_modified':False,'ready_for_gpu':not issues,'counts':counts,'checks':checks,'issues':issues}
    out=ROOT/'.audit-reports'; out.mkdir(exist_ok=True)
    (out/'c003-source-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'source_sha':commit,'ready_for_gpu':not issues,'counts':counts,'issues':issues},ensure_ascii=False,indent=2),flush=True)
    return 0 if not issues else 1

if __name__=='__main__': raise SystemExit(main())
