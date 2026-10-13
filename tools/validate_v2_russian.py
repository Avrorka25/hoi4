#!/usr/bin/env python3
"""Check complete Russian coverage and executable localisation expressions."""
import argparse
import collections
import json
import re
from pathlib import Path

from localise_v2 import english_catalog, russian_catalog, english_paths, target_path, russian_name, safe, PROTECTED, ENTRY

STRICT = re.compile(r'^\s*[^\s:#]+:\d*\s*"(?:[^"\\]|\\.)*"\s*(?:#.*)?$')


def validate(root):
    english = english_catalog(root)
    russian = russian_catalog(root)
    paths = english_paths(root)
    errors, counts = [], collections.Counter()
    for name, source in english.items():
        target_name = russian_name(name)
        if target_name not in russian:
            errors.append(f'Missing Russian file: {target_name}')
            continue
        translated = russian[target_name]
        if source.keys() != translated.keys():
            errors.append(f'{target_name}: key mismatch: missing {sorted(source.keys() - translated.keys())}, added {sorted(translated.keys() - source.keys())}')
        for key, value in source.items():
            if key not in translated:
                continue
            if not safe(value, translated[key]):
                errors.append(f'{target_name}:{key}: changed executable expressions/formatting')
            if re.fullmatch(r'GFX_[A-Za-z0-9_]+', value) and value != translated[key]:
                errors.append(f'{target_name}:{key}: changed functional image identifier')
            counts['covered_file_entries'] += 1
        source_lines = [ENTRY.match(s) for s in paths[name].read_text(encoding='utf-8-sig').splitlines() if ENTRY.match(s)]
        target_lines = [ENTRY.match(s) for s in target_path(root, target_name).read_text(encoding='utf-8-sig').splitlines() if ENTRY.match(s)]
        if len(source_lines) != len(target_lines):
            errors.append(f'{target_name}: localisation line count mismatch')
        for original, target in zip(source_lines, target_lines):
            if original[1] != target[1] or not safe(original[2], target[2]):
                errors.append(f'{target_name}:{original[1]}: line order or expressions changed')
            source_version = re.search(r':(\d*)\s*"', original[0])[1] or '0'
            target_version = re.search(r':(\d*)\s*"', target[0])[1] or '0'
            if source_version != target_version:
                errors.append(f'{target_name}:{original[1]}: localisation version changed')
            counts['covered_source_lines'] += 1
    for name in russian:
        path = target_path(root, name)
        if not path.read_bytes().startswith(b'\xef\xbb\xbf'):
            errors.append(f'{name}: missing UTF-8 BOM')
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        if not lines or lines[0] != 'l_russian:':
            errors.append(f'{name}: incorrect Russian language header')
        seen = set()
        for number, line in enumerate(lines[1:], 2):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            if not STRICT.match(line):
                errors.append(f'{name}:{number}: malformed or unescaped quoted value')
            match = re.match(r'\s*([^\s:#]+):', line)
            if match:
                if match[1] in seen:
                    counts['source_duplicate_lines_preserved'] += 1
                seen.add(match[1])
    # Generated identifiers must be present in both languages.
    en_keys = {k for d in english.values() for k in d}
    ru_keys = {k for d in russian.values() for k in d}
    for path in (root / 'interface').rglob('*.gui'):
        for key in set(re.findall(r'V2_RU_INTERFACE_[A-F0-9]{12}', path.read_text(encoding='utf-8-sig'))):
            counts['interface_key_references'] += 1
            if key not in en_keys or key not in ru_keys:
                errors.append(f'{path.name}: missing interface localisation {key}')
    for directory in ('common/national_focus', 'events', 'common/decisions', 'common/scripted_effects'):
        for path in (root / directory).rglob('*.txt'):
            for key in set(re.findall(r'V2_RU_FACTION_[A-F0-9]{12}', path.read_text(encoding='utf-8-sig'))):
                counts['faction_name_key_references'] += 1
                if key not in en_keys or key not in ru_keys:
                    errors.append(f'{path.name}: missing faction name localisation {key}')
    latin_only = []
    for name, translated in russian.items():
        for key, value in translated.items():
            plain = PROTECTED.sub('', value)
            if re.search(r'[A-Za-z]', plain) and not re.search(r'[А-Яа-яЁё]', plain):
                latin_only.append({'file': name, 'key': key, 'text': value})
    counts.update(english_files=len(english), russian_files=len(russian), unique_english_keys=len(en_keys),
                  unique_russian_keys=len(ru_keys), missing_keys=len(en_keys - ru_keys),
                  latin_only_entries=len(latin_only))
    return {'checks_passed': not errors, 'counts': dict(counts), 'errors': errors,
            'latin_only_entries_for_editorial_review': latin_only, 'runtime_tested': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--audit', type=Path, help='Write Latin-only values for editorial review separately')
    args = parser.parse_args()
    report = validate(args.root)
    audit = report.pop('latin_only_entries_for_editorial_review')
    if args.audit:
        args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report['editorial_review_complete'] = False
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['checks_passed'] else 1)
