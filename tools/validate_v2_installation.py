#!/usr/bin/env python3
"""Check that source + cumulative patch and previous v2 + language patch match.

Hash comparisons are virtual installations: no game code is executed. The
previous-package manifest must be captured before rebuilding the full ZIP.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def manifest_zip(path):
    with zipfile.ZipFile(path) as archive:
        return {n: hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if not n.endswith('/')}


def manifest_directory(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                result[root.name + '/' + path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--previous-manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    full = manifest_zip(repo / 'RT56RP_v2_HOI4_1.19.zip')
    cumulative = manifest_zip(repo / 'RT56RP_v2_HOI4_1.19_patch.zip')
    language = manifest_zip(repo / 'RT56RP_v2_Russian_patch.zip')
    source = manifest_directory(args.original)
    previous = json.loads(args.previous_manifest.read_text())
    factions = json.loads((repo / 'tools/v2_factions_report.json').read_text())
    installed = {**source, **cumulative}
    upgraded = {**previous['files'], **language}
    errors = []
    for label, actual in [('source+cumulative', installed), ('previous_v2+language', upgraded)]:
        different = sorted(n for n in actual.keys() | full.keys() if actual.get(n) != full.get(n))
        if different:
            errors.append({'installation': label, 'different_files': different})
    allowed = ('localisation/', 'interface/', 'descriptor.mod')
    changed = []
    root = args.original.name + '/'
    for name, value in full.items():
        if name.startswith(root) and previous['files'].get(name) != value:
            changed.append(name)
            relative = name.removeprefix(root)
            if not relative.startswith(allowed) and not (relative in factions['changed_files'] and factions['only_faction_display_names_changed']):
                errors.append({'unexpected_gameplay_change': name})
    forbidden = [n for n in full if Path(n).suffix.lower() in ('.exe', '.dll', '.dmp', '.log')]
    if forbidden:
        errors.append({'unexpected_private_or_executable_files': forbidden})
    report = {'checks_passed': not errors, 'full_package_files': len(full),
              'cumulative_patch_files': len(cumulative), 'language_patch_files': len(language),
              'previous_package_sha256': previous['sha256'], 'translation_changed_files': changed,
              'both_virtual_installations_match_full_package': not errors,
              'gameplay_mechanics_unchanged_since_v2fix_1': not any('unexpected_gameplay_change' in e for e in errors),
              'faction_scenarios_changed_only_in_display_names': factions['only_faction_display_names_changed'],
              'runtime_tested': False, 'errors': errors}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'translation_changed_files'}, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(0 if not errors else 1)


if __name__ == '__main__':
    main()
