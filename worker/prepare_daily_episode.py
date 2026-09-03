import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', required=True)
    p.add_argument('--day', type=int, required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--dialogues', default=None)
    args = p.parse_args()

    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    day = next((d for d in plan['days'] if d['day'] == args.day), None)
    if not day:
        raise SystemExit(f'Day {args.day} not found')

    dialogue_pack = None
    dialogue_rules = {}
    dialogue_path = Path(args.dialogues) if args.dialogues else plan_path.with_name('dialogue_overrides.json')
    if dialogue_path.is_file():
        all_dialogues = json.loads(dialogue_path.read_text(encoding='utf-8'))
        dialogue_rules = all_dialogues.get('rules', {})
        dialogue_pack = all_dialogues.get('days', {}).get(str(args.day))

    if dialogue_pack:
        day = dict(day)
        day['dialogue'] = dialogue_pack

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / 'voiceover.txt').write_text(day['voiceover'].strip() + '\n', encoding='utf-8')
    (out / 'episode.json').write_text(json.dumps(day, ensure_ascii=False, indent=2), encoding='utf-8')

    manifest = {
        'series': plan['series'],
        'day': day['day'],
        'title': day['title'],
        'city': day['city'],
        'location': day['location'],
        'outfit': day['outfit'],
        'format': plan['format'],
        'target_duration_seconds': plan['target_duration_seconds'],
        'identity_rules': plan['identity_rules'],
        'dialogue_rules': dialogue_rules,
        'dialogue': dialogue_pack,
        'scenes': []
    }

    dialogue_by_scene = {}
    if dialogue_pack:
        for line in dialogue_pack.get('dialogues', []):
            dialogue_by_scene.setdefault(line['scene'], []).append(line)

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
                f"When AI_CLONE speaks, frame him clearly enough for accurate lip-sync."
            )

        full_prompt = (
            f"Vertical 9:16 cinematic realistic short-film scene in Germany. "
            f"Same persistent AI clone identity as MASTER_PHOTOS. Preserve exact face, beard, hairstyle, age, "
            f"skin tone and natural proportions. No beautification, no identity drift. "
            f"City: {day['city']}. Location: {day['location']}. Outfit: {day['outfit']}. "
            f"Shot: {s['shot']}. Action: {s['prompt']}. Natural body movement, realistic hands, "
            f"authentic German environment, cinematic depth of field, natural lighting. "
            f"Secondary people may appear when the scene requires them; main clone remains central."
            f"{dialogue_instruction}"
        )
        manifest['scenes'].append({
            'n': s['n'],
            'seconds': s['seconds'],
            'shot': s['shot'],
            'prompt': full_prompt,
            'dialogue': scene_dialogue,
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

    (out / 'README.txt').write_text(
        f"DAY {day['day']:02d}: {day['title']}\n"
        f"CITY: {day['city']}\nLOCATION: {day['location']}\nOUTFIT: {day['outfit']}\n"
        f"{dialogue_note}\n"
        "1) Generate scene_01.mp4 ... scene_08.mp4 using scene_prompts.json.\n"
        "2) Keep the same AI clone identity in every scene.\n"
        "3) AI_CLONE lines must use MASTER_VOICE and visible lip-sync.\n"
        "4) Supporting-character lines must use a different voice.\n"
        "5) Run assemble_daily_episode.py to build the final 9:16 short film.\n",
        encoding='utf-8'
    )
    print(f'✅ Daily episode pack ready: {out}')
    if dialogue_pack:
        print('🗣️ Dialogue episode:', dialogue_pack.get('secondary_character'))


if __name__ == '__main__':
    main()
