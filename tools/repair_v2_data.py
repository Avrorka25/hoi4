"""Repair concrete localization and victory-point errors in the uploaded fork."""
import collections
import re

from clausewitz import parse, walk
from map_buildings import state_provinces
from validate_mod import ENTRY


def repair_localization(root):
    changes = []
    broken_quotes = {
        'TWR_modifiericons_l_english.yml': {'KRA_Koysginomics_Copy_Foreign_Research_tt'},
        'r56rp_countries_cosmetic_l_english.yml': {'GER_ZIM_IMP_ADJ'},
        'r56_china_and_co_l_english.yml': {'KMT_align_with_moscow_desc', 'KMT_prepare_national_liberation_desc',
                                        'CHI_transfer_factories_to_the_proletariat_desc', 'CHI_a_bulwark_of_defense_desc'},
        'INS_l_english.yml': {'RULE_OPTION_FASCIST_INS_AI_DESC'},
        'r56rp_formables_interface_l_english.yml': {'formable_nations_amazonia_category_desc'},
        'research_l_english.yml': {'radio_detection_sp_2_tt'},
        'r56rp_tools_l_english.yml': {'WHITEPEACE'},
        'r56_startup_l_spanish.yml': {'r56.startup.event.01.description'},
    }
    for path in (root / 'localisation').rglob('*.yml'):
        text = path.read_text(encoding='utf-8-sig')
        lines = text.splitlines()
        changed = False
        for i, line in enumerate(lines):
            if re.fullmatch(r'\s*l_[a-z_]+:0\s*', line):
                lines[i] = line.replace(':0', ':')
                changes.append({'kind': 'language_header', 'file': str(path.relative_to(root))})
                changed = True
                continue
            key = re.match(r'^\s*([^\s:]+):', line)
            if not key or key[1] not in broken_quotes.get(path.name, set()) or ENTRY.match(line):
                continue
            if key[1] == 'GER_ZIM_IMP_ADJ':
                lines[i] = line.rstrip().removesuffix("'") + '"'
            elif key[1] == 'WHITEPEACE':
                lines[i] = line.split('"first_marked_state', 1)[0] + '"'
            elif key[1] == 'r56.startup.event.01.description':
                if not line.rstrip().endswith('".'):
                    raise ValueError('Unexpected Spanish localization layout')
                lines[i] = line.rstrip()[:-2] + '."'
            elif line.rstrip().endswith('”'):
                lines[i] = line.rstrip()[:-1] + '"'
            else:
                lines[i] = line + '"'
            if not ENTRY.match(lines[i]):
                raise ValueError(f'Localization repair failed: {path.name}:{key[1]}')
            changes.append({'kind': 'localization_quote', 'file': str(path.relative_to(root)), 'key': key[1]})
            changed = True
        if changed:
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    return changes


def repair_victory_points(root):
    states = state_provinces(root, root)
    owners = {p: s for s, provinces in states.items() for p in provinces}
    paths, bad, edits = {}, [], collections.defaultdict(list)
    for path in (root / 'history/states').glob('*.txt'):
        tree = parse(path.read_text(encoding='utf-8-sig'))
        state = next(n for n in tree['children'] if n['key'] == 'state')
        sid = int(state['values']['id'][0])
        paths[sid] = path
        def visit(node, parents=()):
            if node['key'] == 'victory_points' and node['atoms']:
                province = int(node['atoms'][0])
                if province not in states[sid]:
                    value = node['atoms'][1]
                    if parents != ('state', 'history') or province not in owners:
                        raise ValueError('Unexpected dated or unknown misplaced victory point')
                    bad.append((sid, owners[province], province, value))
                    edits[path].append(node)
            for child in node['children']:
                visit(child, parents + (node['key'],))
        visit(state)
    for path, nodes in edits.items():
        text = path.read_text(encoding='utf-8-sig')
        for node in sorted(nodes, key=lambda n: n['declaration_start'], reverse=True):
            text = text[:node['declaration_start']] + text[node['end'] + 1:]
        path.write_text(text, encoding='utf-8')
    changes = []
    for sid, target, province, value in bad:
        path = paths[target]
        text = path.read_text(encoding='utf-8-sig')
        state = next(n for n in parse(text)['children'] if n['key'] == 'state')
        history = next(n for n in state['children'] if n['key'] == 'history')
        existing = next((n for n in history['children'] if n['key'] == 'victory_points' and
                         n['atoms'] and n['atoms'][0] == str(province)), None)
        if not existing:
            text = text[:history['end']] + f'\n\t\tvictory_points = {{ {province} {value} }}\n\t' + text[history['end']:]
            path.write_text(text, encoding='utf-8')
        changes.append({'kind': 'victory_point_state', 'province': province,
                        'from_state': sid, 'to_state': target, 'source_value': value,
                        'correct_state_already_had_point': existing is not None,
                        'retained_value': existing['atoms'][1] if existing else value})
    return changes


def repair(root):
    return repair_localization(root) + repair_victory_points(root)
