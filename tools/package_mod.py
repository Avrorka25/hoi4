#!/usr/bin/env python3
"""Package the reviewed mod and a cumulative overlay; never overwrite the source archive."""
import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original-directory', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    package = repo / 'Road_to_56_RP_fixed'
    root = package / 'Road_to_56_RP'
    version = re.search(r'version\s*=\s*"([^"]+)"', (root / 'descriptor.mod').read_text())[1]
    expected_source_sha = '42ff8f2b345f5406c698df134aafa76eb251c89b8cd58c5c32b0e495fd9b97be'
    if digest(repo / 'The Road to 56 RP.zip') != expected_source_sha:
        raise ValueError('Original archive checksum differs; inspect the new source before packaging')
    original = {p.relative_to(args.original_directory).as_posix(): p
                for p in args.original_directory.rglob('*') if p.is_file()}
    current = {p.relative_to(root).as_posix(): p for p in root.rglob('*') if p.is_file()}
    removed = sorted(original.keys() - current.keys())
    if removed:
        raise ValueError(f'Overlay cannot delete files: {removed}')
    changed = sorted(name for name in original if digest(original[name]) != digest(current[name]))
    added = sorted(current.keys() - original.keys())
    for name in changed:
        if current[name].suffix.lower() not in {'.txt', '.gui', '.gfx', '.asset', '.mod', '.lua', '.yml'}:
            raise ValueError(f'Unexpected media/binary modification: {name}')
    for name in ['README_RU.md', 'CRASH_ANALYSIS_RU.md']:
        shutil.copyfile(repo / name, package / name)
    report_path = package / 'REPORT.json'
    report = json.loads(report_path.read_text())
    reset_files = sorted(json.loads((repo / 'tools/startup_diagnostic_report.json').read_text())['files'])
    if any(not (root / name).is_file() for name in reset_files):
        raise ValueError('Diagnostic rollback source is incomplete')
    report.update({
        'package_version': version, 'unchanged_files': len(original) - len(changed),
        'changed_files': changed, 'added_files': added, 'removed_files': removed,
        'validation': json.loads((repo / 'tools/validation_report.json').read_text()),
        'crash_regression': json.loads((repo / 'tools/crash_regression_report.json').read_text()),
        'runtime_crash_confirmed_fixed': False,
        'diagnostic_overlay_reset_files': reset_files,
    })
    if (repo / 'tools/crash_map_fix_report.json').exists():
        report['crash_map_fix'] = json.loads((repo / 'tools/crash_map_fix_report.json').read_text())
        if report.get('reference_rt56_commit'):
            report['previous_reference_rt56_commit'] = report['reference_rt56_commit']
        report['reference_rt56_commit'] = None
        report['reference_rt56_archive'] = 'The_Road_to_56.zip'
        report['reference_rt56_archive_sha256'] = digest(repo / 'The_Road_to_56.zip')
    if not report['validation']['static_checks_passed']:
        raise ValueError('Static checks failed')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output = []
    for filename, overlay in [('Road_to_56_RP_fixed.zip', False), ('Road_to_56_RP_patch.zip', True)]:
        target = repo / filename
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            if overlay:
                for name in sorted(set(changed + added + reset_files)):
                    archive.write(current[name], 'Road_to_56_RP/' + name)
                for name in ['Road_to_56_RP.mod', 'README_RU.md', 'CRASH_ANALYSIS_RU.md', 'REPORT.json']:
                    archive.write(package / name, name)
                archive.writestr('README_PATCH_RU.md',
                    f'Патч {version} накладывается на исходный мод, rufix.1 или rufix.2.\n'
                    'Также заменяются файлы диагностического пакета исходными рабочими скриптами.\n'
                    'Если мод ещё не установлен, сначала скопируйте все исходные файлы в\n'
                    'Documents/Paradox Interactive/Hearts of Iron IV/mod/Road_to_56_RP.\n'
                    'Скопируйте папку Road_to_56_RP и внешний Road_to_56_RP.mod из патча\n'
                    'в папку mod с заменой всех файлов, включая map/buildings.txt.\n'
                    'Включите только одну RP-версию вместе с The Road to 56.\n'
                    'Прочитайте README_RU.md и CRASH_ANALYSIS_RU.md для проверки запуска.\n')
            else:
                for path in sorted(package.rglob('*')):
                    if path.is_file():
                        archive.write(path, path.relative_to(package).as_posix())
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:
                raise ValueError(f'ZIP CRC failed: {filename}')
        output.append(f'{digest(target)}  {filename}')
        print(filename, target.stat().st_size, 'bytes')
    output.append(f'{expected_source_sha}  The Road to 56 RP.zip')
    for name in ['The_Road_to_56.zip', 'Road_to_56_RUS.zip',
                 'Road_to_56_RP_diagnostic.zip', 'Road_to_56_RP_diagnostic_rollback.zip']:
        if (repo / name).is_file():
            output.append(f'{digest(repo / name)}  {name}')
    (repo / 'SHA256SUMS.txt').write_text('\n'.join(output) + '\n')
    print('Changed:', len(changed), 'added:', len(added), 'removed:', len(removed))


if __name__ == '__main__':
    main()
