#!/usr/bin/env python3
"""Build a reversible overlay which only adds script log statements."""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

TOKEN = re.compile(r'#[^\n]*|"(?:\\.|[^"\\])*"|[{}=]|[^\s{}=#"]+')


def instrument_actions(text, relative):
    stack, previous, spans = [], [], []
    for match in TOKEN.finditer(text):
        token = match.group()
        if token.startswith('#'):
            continue
        if token == '{':
            key = previous[-2] if len(previous) > 1 and previous[-1] == '=' else ''
            selected = key == 'effect' and stack and stack[-1][0] == 'on_startup'
            stack.append((key, match.end(), selected))
        elif token == '}':
            key, start, selected = stack.pop()
            if selected:
                spans.append((start, match.start()))
        previous.append(token)
    insertions = []
    for number, (start, end) in enumerate(sorted(spans), 1):
        label = f'{relative}|BLOCK={number}'
        insertions.append((start, f'\n\t\t\tlog = "RPDIAG|START|{label}"\n'))
        insertions.append((end, f'\n\t\t\tlog = "RPDIAG|END|{label}"\n\t\t'))
    if relative == 'common/on_actions/rt56rp_on_actions.txt':
        start_marker = '#generate characters - remove generic silent workhorse'
        end_marker = '#set country-specific eco laws'
        assert text.count(start_marker) == text.count(end_marker) == 1
        for marker, message in [(start_marker, 'START'), (end_marker, 'END')]:
            insertions.append((text.index(marker), f'log = "RPDIAG|{message}|CHARACTER_RETIREMENT"\n\t\t\t'))
    for offset, addition in sorted(insertions, reverse=True):
        text = text[:offset] + addition + text[offset:]
    return text, len(spans)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_directory
    output.mkdir(parents=True, exist_ok=True)
    changes, blocks = {}, 0
    for path in sorted((args.root / 'common/on_actions').glob('*.txt')):
        relative = path.relative_to(args.root).as_posix()
        # Preserve BOM and original line endings; modifications are insertions only.
        source = path.read_bytes().decode('utf-8')
        modified, count = instrument_actions(source, relative)
        if count:
            changes[relative] = (source.encode('utf-8'), modified.encode('utf-8'))
            blocks += count
    relative = 'history/general/generic_advisors.txt'
    source = (args.root / relative).read_bytes()
    bom = b'\xef\xbb\xbf' if source.startswith(b'\xef\xbb\xbf') else b''
    modified = (bom + b'log = "RPDIAG|START|history/general/generic_advisors.txt"\n'
                + source[len(bom):] + b'\nlog = "RPDIAG|END|history/general/generic_advisors.txt"\n')
    changes[relative] = (source, modified)
    for filename, apply in [('Road_to_56_RP_diagnostic.zip', True), ('Road_to_56_RP_diagnostic_rollback.zip', False)]:
        with zipfile.ZipFile(output / filename, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, (source, modified) in changes.items():
                archive.writestr('Road_to_56_RP/' + relative, modified if apply else source)
            archive.write(output / 'DIAGNOSTIC_RU.md', 'DIAGNOSTIC_RU.md')
    report = {
        'basis': '1.19-rufix.2', 'runtime_crash_fixed': False,
        'purpose': 'Add log-only markers for a new-game crash; do not change gameplay logic.',
        'instrumented_startup_effect_blocks': blocks,
        'files': {relative: {'original_sha256': hashlib.sha256(source).hexdigest(),
                            'diagnostic_sha256': hashlib.sha256(modified).hexdigest()}
                  for relative, (source, modified) in changes.items()},
        'archives': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                     for name in ['Road_to_56_RP_diagnostic.zip', 'Road_to_56_RP_diagnostic_rollback.zip']},
    }
    (output / 'tools/startup_diagnostic_report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
