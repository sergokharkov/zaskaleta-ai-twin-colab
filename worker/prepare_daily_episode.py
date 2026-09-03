import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', required=True)
    p.add_argument('--day', type=int, required=True)
    p.add_argument('--output-dir', required=True)
    args = p.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
    day = next((d for d in plan['days'] if d['day'] == args.day), None)
    if not day:
        raise SystemExit(f'Day {args.day} not found')

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
        'scenes': []
    }
    for s in day['scenes']:
        full_prompt = (
            f"Vertical 9:16 cinematic realistic short-film scene in Germany. "
            f"Same persistent AI clone identity as MASTER_PHOTOS. Preserve exact face, beard, hairstyle, age, "
            f"skin tone and natural proportions. No beautification, no identity drift. "
            f"City: {day['city']}. Location: {day['location']}. Outfit: {day['outfit']}. "
            f"Shot: {s['shot']}. Action: {s['prompt']}. Natural body movement, realistic hands, "
            f"authentic German environment, cinematic depth of field, natural lighting. "
            f"Secondary people may appear only when the scene requires them; main clone remains central."
        )
        manifest['scenes'].append({
            'n': s['n'],
            'seconds': s['seconds'],
            'shot': s['shot'],
            'prompt': full_prompt,
            'expected_clip': f"scene_{s['n']:02d}.mp4"
        })

    (out / 'scene_prompts.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'README.txt').write_text(
        f"DAY {day['day']:02d}: {day['title']}\n"
        f"CITY: {day['city']}\nLOCATION: {day['location']}\nOUTFIT: {day['outfit']}\n\n"
        "1) Generate scene_01.mp4 ... scene_08.mp4 using scene_prompts.json.\n"
        "2) Keep the same AI clone identity in every scene.\n"
        "3) Use voiceover.txt as the Ukrainian narration.\n"
        "4) Run assemble_daily_episode.py to build the final 9:16 short film.\n",
        encoding='utf-8'
    )
    print(f'✅ Daily episode pack ready: {out}')


if __name__ == '__main__':
    main()
