#!/usr/bin/env python3
"""Build a full standalone mod and a cumulative patch while retaining mod.zip."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from update_v2 import SOURCE_SHA256, VERSION


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    if digest(repo / 'mod.zip') != SOURCE_SHA256:
        raise ValueError('Source mod.zip was modified')
    validation = json.loads((repo / 'tools/v2_validation_report.json').read_text())
    if not validation['static_checks_passed']:
        raise ValueError('Standalone mod validation failed')
    original = {p.relative_to(args.original).as_posix(): p for p in args.original.rglob('*') if p.is_file()}
    current = {p.relative_to(args.root).as_posix(): p for p in args.root.rglob('*') if p.is_file()}
    removed = sorted(original.keys() - current.keys())
    if removed:
        raise ValueError(f'Cannot apply a cumulative patch with deleted files: {removed}')
    changed = sorted(n for n in original if digest(original[n]) != digest(current[n]))
    added = sorted(current.keys() - original.keys())
    for name in changed:
        if current[name].suffix not in ('.txt', '.yml', '.mod'):
            raise ValueError(f'Unexpected asset modification: {name}')
    package = args.root.parent
    shutil.copyfile(repo / 'README_V2_119_RU.md', package / 'README_V2_119_RU.md')
    report = {'package_version': VERSION, 'source_archive': 'mod.zip', 'source_archive_sha256': SOURCE_SHA256,
              'changed_files': changed, 'added_files': added, 'removed_files': removed,
              'unchanged_files': len(original) - len(changed),
              'validation': validation, 'changes': json.loads((repo / 'tools/v2_update_report.json').read_text()),
              'preservation_checks': json.loads((repo / 'tools/v2_regression_report.json').read_text()),
              'runtime_tested': False, 'graphics_sound_and_province_map_unchanged': True}
    (package / 'REPORT_V2.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    sums = []
    for name, patch in [('RT56RP_v2_HOI4_1.19.zip', False), ('RT56RP_v2_HOI4_1.19_patch.zip', True)]:
        target = repo / name
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in sorted(set(changed + added) if patch else current):
                archive.write(current[relative], args.root.name + '/' + relative)
            for extra in [args.root.name + '.mod', 'README_V2_119_RU.md', 'REPORT_V2.json']:
                archive.write(package / extra, extra)
        with zipfile.ZipFile(target) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise ValueError(f'ZIP CRC error: {bad}')
        sums.append(f'{digest(target)}  {name}')
        print(name, target.stat().st_size, 'bytes', flush=True)
    sums.append(f'{SOURCE_SHA256}  mod.zip')
    (repo / 'SHA256SUMS_V2.txt').write_text('\n'.join(sums) + '\n')
    (repo / 'tools/v2_package_report.json').write_text(json.dumps({
        'package_version': VERSION, 'changed_files': changed, 'added_files': added, 'removed_files': removed,
        'unchanged_files': len(original) - len(changed), 'graphics_sound_and_province_map_unchanged': True,
        'zip_crc_checks_passed': True, 'runtime_tested': False}, ensure_ascii=False, indent=2) + '\n')
    print('Changed:', len(changed), 'added:', len(added), 'removed:', len(removed), flush=True)


if __name__ == '__main__':
    main()
