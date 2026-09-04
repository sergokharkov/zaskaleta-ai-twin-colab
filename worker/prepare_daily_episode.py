import argparse
import json
import re
from pathlib import Path


def _repair_scalar_line(line: str, key: str) -> str:
    marker = f'"{key}": "'
    if marker not in line:
        return line
    prefix, rest = line.split(marker, 1)
    suffix = '",'
    if not rest.endswith(suffix):
        return line
    body = rest[:-2]
    out = []
    opening = True
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"' and (i == 0 or body[i - 1] != '\\'):
            out.append('«' if opening else '»')
            opening = not opening
        else:
            out.append(ch)
        i += 1
    return prefix + marker + ''.join(out) + suffix


def load_json_tolerant(path: Path):
    text = path.read_text(encoding='utf-8')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        keys = ('voiceover', 'hook', 'title', 'city', 'location', 'outfit')
        fixed_lines = []
        for line in text.splitlines():
            repaired = line
            for key in keys:
                repaired = _repair_scalar_line(repaired, key)
            fixed_lines.append(repaired)
        fixed = '\n'.join(fixed_lines)
        return json.loads(fixed)


def flatten_realism(realism_lock):
    parts = []
    for group, rules in (realism_lock or {}).items():
        if isinstance(rules, list) and rules:
            parts.append(f"{group}: " + '; '.join(str(x) for x in rules))
    return ' | '.join(parts)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', required=True)
    p.add_argument('--day', type=int, required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--dialogues', default=None)
    p.add_argument('--outfits', default=None)
    p.add_argument('--clone-profile', default=None)
    args = p.parse_args()

    plan_path = Path(args.plan)
    plan = load_json_tolerant(plan_path)
    day = next((d for d in plan['days'] if d['day'] == args.day), None)
    if not day:
        raise SystemExit(f'Day {args.day} not found')
    day = dict(day)

    dialogue_pack = None
    dialogue_rules = {}
    dialogue_path = Path(args.dialogues) if args.dialogues else plan_path.with_name('dialogue_overrides.json')
    if dialogue_path.is_file():
        all_dialogues = json.loads(dialogue_path.read_text(encoding='utf-8'))
        dialogue_rules = all_dialogues.get('rules', {})
        dialogue_pack = all_dialogues.get('days', {}).get(str(args.day))
    if dialogue_pack:
        day['dialogue'] = dialogue_pack

    outfit_profile = None
    outfit_rules = {}
    outfit_path = Path(args.outfits) if args.outfits else plan_path.with_name('outfit_profiles.json')
    if outfit_path.is_file():
        outfits = json.loads(outfit_path.read_text(encoding='utf-8'))
        outfit_rules = outfits.get('rules', {})
        profile_id = outfits.get('day_assignment', {}).get(str(args.day))
        if profile_id:
            profile = outfits.get('profiles', {}).get(profile_id)
            if profile:
                outfit_profile = {'id': profile_id, **profile}
                day['outfit_profile'] = outfit_profile
                day['outfit'] = profile.get('description', day.get('outfit', ''))

    clone_profile = {}
    clone_path = Path(args.clone_profile) if args.clone_profile else plan_path.with_name('clone_reference_profile.json')
    if clone_path.is_file():
        clone_profile = json.loads(clone_path.read_text(encoding='utf-8'))
    realism_lock = clone_profile.get('realism_lock', {})
    realism_text = flatten_realism(realism_lock)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / 'voiceover.txt').write_text(day['voiceover'].strip() + '\n', encoding='utf-8')
    day['clone_profile'] = clone_profile.get('profile_name')
    (out / 'episode.json').write_text(json.dumps(day, ensure_ascii=False, indent=2), encoding='utf-8')

    manifest = {
        'series': plan['series'],
        'day': day['day'],
        'title': day['title'],
        'city': day['city'],
        'location': day['location'],
        'outfit': day['outfit'],
        'outfit_profile': outfit_profile,
        'outfit_rules': outfit_rules,
        'format': plan['format'],
        'target_duration_seconds': plan['target_duration_seconds'],
        'identity_rules': plan['identity_rules'],
        'realism_lock': realism_lock,
        'dialogue_rules': dialogue_rules,
        'dialogue': dialogue_pack,
        'scenes': []
    }

    dialogue_by_scene = {}
    if dialogue_pack:
        for line in dialogue_pack.get('dialogues', []):
            dialogue_by_scene.setdefault(line['scene'], []).append(line)

    outfit_prompt = outfit_profile.get('prompt', day['outfit']) if outfit_profile else day['outfit']

    for s in day['scenes']:
        scene_dialogue = dialogue_by_scene.get(s['n'], [])
        dialogue_instruction = ''
        if scene_dialogue:
            lines = ' '.join(
                f"{x['speaker']} says in Ukrainian: «{x['text']}»" for x in scene_dialogue
            )
            secondary = dialogue_pack.get('secondary_character', 'supporting character')
            relationship = dialogue_pack.get('relationship', 'supporting role')
            dialogue_instruction = (
                f" Dialogue scene. Supporting character: {secondary}. Relationship/context: {relationship}. "
                f"Spoken lines: {lines}. Keep the AI clone as the visual and narrative lead. "
                f"Use natural reverse-shot coverage when more than one person speaks. "
                f"When AI_CLONE speaks, frame him clearly enough for accurate lip-sync."
            )

        full_prompt = (
            f"Vertical 9:16 photorealistic cinematic short-film scene in Germany. "
            f"Same persistent AI clone identity as MASTER_PHOTOS and behavior references. Preserve exact face, beard, hairstyle, age, "
            f"skin tone, natural facial asymmetry and body proportions. No beautification, no identity drift. "
            f"City: {day['city']}. Location: {day['location']}. "
            f"Main character outfit is LOCKED for this episode: {outfit_prompt}. "
            f"Do not change clothing, colors, footwear or outer layer between scenes unless the script explicitly requires it. "
            f"Shot: {s['shot']}. Action: {s['prompt']}. Natural blinking, subtle breathing, micro head movement, "
            f"realistic shoulder/neck motion, realistic five-finger hands, authentic German environment, cinematic depth of field, motivated natural lighting. "
            f"Secondary people may appear when the scene requires them; main clone remains central. "
            f"Realism lock: {realism_text}."
            f"{dialogue_instruction}"
        )
        manifest['scenes'].append({
            'n': s['n'],
            'seconds': s['seconds'],
            'shot': s['shot'],
            'prompt': full_prompt,
            'dialogue': scene_dialogue,
            'outfit_profile_id': outfit_profile['id'] if outfit_profile else None,
            'expected_clip': f"scene_{s['n']:02d}.mp4"
        })

    (out / 'scene_prompts.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    if dialogue_pack:
        dialogue_note = (
            f"\nDIALOGUE EPISODE: YES\nSECONDARY CHARACTER: {dialogue_pack.get('secondary_character')}\n"
            f"RELATIONSHIP: {dialogue_pack.get('relationship')}\n"
            "Use separate secondary-character voice for their lines; MASTER_VOICE is reserved for AI_CLONE.\n"
        )
    else:
        dialogue_note = "\nDIALOGUE EPISODE: NO\n"

    outfit_note = ''
    if outfit_profile:
        outfit_note = (
            f"OUTFIT PROFILE: {outfit_profile['id']} — {outfit_profile.get('name_uk')}\n"
            f"OUTFIT LOCK: {outfit_profile.get('description')}\n"
        )

    (out / 'README.txt').write_text(
        f"DAY {day['day']:02d}: {day['title']}\n"
        f"CITY: {day['city']}\nLOCATION: {day['location']}\n"
        f"CLONE PROFILE: {clone_profile.get('profile_name', 'persistent clone')}\n"
        f"{outfit_note}OUTFIT: {day['outfit']}\n"
        f"{dialogue_note}\n"
        "REALISM LOCK: ON\n"
        "1) Generate scene_01.mp4 ... scene_08.mp4 using scene_prompts.json.\n"
        "2) Keep the same AI clone identity in every scene.\n"
        "3) Keep the assigned outfit profile visually consistent across the whole episode.\n"
        "4) AI_CLONE lines must use MASTER_VOICE and visible lip-sync.\n"
        "5) Supporting-character lines must use a different voice.\n"
        "6) Use behavior-reference videos for natural facial motion, speech rhythm and gesture style; do not copy their low camera angle.\n"
        "7) Run assemble_daily_episode.py to build the final 9:16 short film.\n",
        encoding='utf-8'
    )
    print(f'✅ Daily episode pack ready: {out}')
    if realism_lock:
        print('🎭 Realism lock: ON')
    if outfit_profile:
        print('👕 Outfit profile:', outfit_profile['id'], '—', outfit_profile.get('name_uk'))
    if dialogue_pack:
        print('🗣️ Dialogue episode:', dialogue_pack.get('secondary_character'))


if __name__ == '__main__':
    main()
