#!/usr/bin/env python3
"""Reproduce the reviewed 1.19 update of the user's standalone RT56 RP fork."""
import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

from clausewitz import parse, walk

SOURCE_SHA256 = '2f5804081f8c7cd7c5debe017b67c5b8f633fd350a48b5972e144758b4fe8f9d'
VERSION = '1.19-v2fix.1'
CATEGORIES = ('technologies', 'characters', 'ideas', 'traits', 'organizations',
              'equipment', 'dynamic_modifiers', 'modifiers', 'subdoctrines', 'focuses')
DIRECTORIES = {
    'technologies': 'common/technologies', 'characters': 'common/characters',
    'ideas': 'common/ideas', 'traits': 'common/country_leader',
    'organizations': 'common/military_industrial_organization/organizations',
    'equipment': 'common/units/equipment', 'dynamic_modifiers': 'common/dynamic_modifiers',
    'modifiers': 'common/modifiers', 'subdoctrines': 'common/doctrines/subdoctrines',
    'focuses': 'common/national_focus',
}


def definitions(root, category):
    result = collections.defaultdict(list)
    for path in sorted((root / DIRECTORIES[category]).rglob('*.txt')):
        text = path.read_text(encoding='utf-8-sig')
        tree = parse(text)
        if category == 'focuses':
            nodes = [n for n in walk(tree) if n['key'] in ('focus', 'shared_focus', 'joint_focus') and 'id' in n['values']]
        elif category == 'ideas':
            nodes = [n for top in tree['children'] if top['key'] == 'ideas'
                     for group in top['children'] for n in group['children']]
        elif category in ('technologies', 'characters', 'traits', 'equipment'):
            container = {'technologies': 'technologies', 'characters': 'characters',
                         'traits': 'leader_traits', 'equipment': 'equipments'}[category]
            nodes = [n for top in tree['children'] if top['key'] == container for n in top['children']]
        else:
            nodes = tree['children']
        for node in nodes:
            identity = node['values'].get('id', [node['key']])[0] if category == 'focuses' else node['key']
            result[identity].append((path, node, text))
    return result


def canonical(node):
    return (node['key'], node['values'], node['atoms'], [canonical(n) for n in node['children']])


def remove_blocks(path, nodes):
    text = path.read_text(encoding='utf-8-sig')
    for node in sorted(nodes, key=lambda n: n['declaration_start'], reverse=True):
        text = text[:node['declaration_start']] + text[node['end'] + 1:]
    # Leave an override file rather than deleting it: cumulative patches cannot
    # delete a previous copy, and deleting an override could expose vanilla data.
    path.write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path, help='A separate working copy; never use the source extraction')
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    with args.source_archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != SOURCE_SHA256:
            raise ValueError('Source mod.zip checksum differs from the reviewed upload')
    root, changes = args.root, []
    if root.resolve() == args.source_archive.parent.resolve():
        raise ValueError('Use a separate extracted working directory')

    submarine = root / 'common/doctrines/subdoctrines/sea/navy_submarine_doctrines.txt'
    from validate_mod import brace_errors
    text = submarine.read_text(encoding='utf-8-sig')
    if brace_errors(text) == ['unclosed braces at [300]']:
        submarine.write_text(text.rstrip() + '\n}\n', encoding='utf-8')
        changes.append({'kind': 'unclosed_submarine_doctrine', 'file': str(submarine.relative_to(root))})
    elif brace_errors(text):
        raise ValueError('Unexpected submarine doctrine syntax problem')

    removed = collections.Counter()
    for category in CATEGORIES:
        edits = collections.defaultdict(list)
        for identity, entries in definitions(root, category).items():
            primary = [(p, n, t) for p, n, t in entries if 'miyagipatch' not in p.name]
            aliases = [(p, n, t) for p, n, t in entries if 'miyagipatch' in p.name]
            if not primary:
                continue
            for path, node, _ in aliases:
                is_stub = category == 'focuses' and ('validation_stubs' in path.name or 'joint_focus_anchor' in path.name)
                if is_stub or any(canonical(node) == canonical(n) for _, n, _ in primary):
                    edits[path].append(node)
                    removed[category] += 1
                    changes.append({'kind': 'duplicate_definition', 'category': category,
                                    'id': identity, 'file': str(path.relative_to(root)),
                                    'retained_files': sorted({str(p.relative_to(root)) for p, _, _ in primary})})
        for path, nodes in edits.items():
            remove_blocks(path, nodes)

    # Three Polish focus technologies differ in balance; keep the country's
    # own definition, which was already the later file in the source archive.
    polish = {'POL_armor_training_focus', 'POL_artillery_training_focus',
              'POL_engineers_special_forces_training_focus'}
    techs = definitions(root, 'technologies')
    edits = collections.defaultdict(list)
    for identity in polish:
        entries = techs.get(identity, [])
        if any(p.name == 'r56_country_techs.txt' for p, _, _ in entries):
            for path, node, _ in entries:
                if path.name == 'infantry_extra_tech.txt':
                    edits[path].append(node)
                    changes.append({'kind': 'duplicate_polish_technology', 'id': identity,
                                    'file': str(path.relative_to(root)), 'retained_file': 'common/technologies/r56_country_techs.txt'})
    for path, nodes in edits.items():
        remove_blocks(path, nodes)

    # The early joint-focus anchor duplicated a real Indonesian focus. Load
    # the real tree early instead, retaining an empty same-path vanilla override.
    indonesia = root / 'common/national_focus/indonesia_taog.txt'
    early = root / 'common/national_focus/000_indonesia_taog_119.txt'
    if not early.exists():
        early.write_bytes(indonesia.read_bytes())
        indonesia.write_text('# Real RP Indonesian tree is in 000_indonesia_taog_119.txt.\n', encoding='utf-8')
        changes.append({'kind': 'early_real_joint_focus_tree', 'file': str(early.relative_to(root))})

    peace = root / 'common/on_actions/pd_on_action.txt'
    text = peace.read_text(encoding='utf-8-sig')
    marker = '\t\t\t\tset_temp_variable = { pts = num_armies_in_state@FROM.FROM }'
    if 'check_variable = { tot_pts > 0 }' not in text:
        before = '\t\t\t\t\thas_war_with = FROM\n\t\t\t\t}\n' + marker
        after = '\t\t\t\t\thas_war_with = FROM\n\t\t\t\t\tcheck_variable = { tot_pts > 0 }\n\t\t\t\t}\n' + marker
        if text.count(before) != 1:
            raise ValueError('Unexpected peace-points loop layout')
        peace.write_text(text.replace(before, after), encoding='utf-8')
        changes.append({'kind': 'zero_division_guard', 'file': str(peace.relative_to(root))})

    # Restore real, absent RT56 technologies referenced by this fork. Include
    # their path dependencies; do not manufacture no-op validation stubs.
    techs = definitions(root, 'technologies')
    base_techs = definitions(args.base, 'technologies')
    seeds = {'mountaineers_a', 'the_thunder_at_our_gates_artillery', 'jungle_tanks'}
    for entries in techs.values():
        for _, node, _ in entries:
            for n in walk(node):
                seeds.update(v for v in n['values'].get('leads_to_tech', []) if v in base_techs)
    pending, imported = sorted(seeds - techs.keys()), {}
    while pending:
        identity = pending.pop(0)
        if identity in techs or identity in imported:
            continue
        if identity not in base_techs or len(base_techs[identity]) != 1:
            raise ValueError(f'No unique reference technology: {identity}')
        path, node, text = base_techs[identity][0]
        imported[identity] = text[node['declaration_start']:node['end'] + 1]
        for n in walk(node):
            pending.extend(v for v in n['values'].get('leads_to_tech', []) if v in base_techs and v not in techs and v not in imported)
    if imported:
        (root / 'common/technologies/v2_119_restored_techs.txt').write_text(
            '# Real definitions from the supplied RT56 1.19 archive.\ntechnologies = {\n' +
            '\n\n'.join(imported[k] for k in sorted(imported)) + '\n}\n', encoding='utf-8')
        changes.append({'kind': 'restored_referenced_technologies', 'ids': sorted(imported)})

    # These RP-prefixed targets are absent, including from vanilla. Their
    # definitions were removed in the uploaded fork: end the existing branches.
    obsolete = {'r56rp_specops_combined_4', 'r56rp_aa_cannon_3'}
    for path in (root / 'common/technologies').glob('*.txt'):
        text = path.read_text(encoding='utf-8-sig')
        nodes = [n for n in walk(parse(text)) if n['key'] == 'path' and
                 any(v in obsolete for v in n['values'].get('leads_to_tech', []))]
        if nodes:
            remove_blocks(path, nodes)
            changes.append({'kind': 'removed_paths_to_absent_rp_techs', 'file': str(path.relative_to(root)), 'count': len(nodes)})

    from repair_v2_data import repair
    changes.extend(repair(root))

    descriptor = (f'version="{VERSION}"\nname="Road to 56 RP v2 (HOI4 1.19)"\n'
                  'tags={ "Alternative History" "Gameplay" }\npicture="thumbnail.png"\n'
                  'supported_version="1.19.*"\nreplace_path="history/states"\n'
                  'replace_path="map/strategicregions"\n')
    if not (root / 'thumbnail.png').exists():
        descriptor = descriptor.replace('picture="thumbnail.png"\n', '')
    (root / 'descriptor.mod').write_text(descriptor, encoding='utf-8')
    (root.parent / (root.name + '.mod')).write_text(descriptor + f'path="mod/{root.name}"\n', encoding='utf-8')
    args.report.write_text(json.dumps({'version': VERSION, 'source_archive_sha256': SOURCE_SHA256,
                                     'duplicate_aliases_removed': dict(removed),
                                     'changes': changes, 'runtime_tested': False}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'duplicate_aliases_removed': dict(removed), 'restored_technologies': sorted(imported),
                      'changes_recorded': len(changes)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
