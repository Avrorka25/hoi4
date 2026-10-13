#!/usr/bin/env python3
"""Validate the standalone mod's own map and 1.19 definitions without executing it."""
import argparse
import collections
import ctypes
import ctypes.util
import json
import re
import struct
from pathlib import Path

from clausewitz import parse, walk
from map_buildings import ProvinceMap, check_ports, check_state_sites, state_provinces
from update_v2 import CATEGORIES, definitions
from validate_mod import brace_errors, event_conflicts, ENTRY


def validate(root):
    errors, warnings, counts = [], [], collections.Counter()
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        relative = path.relative_to(root).as_posix()
        if suffix in ('.txt', '.gui', '.gfx', '.asset', '.mod', '.lua', '.yml'):
            try:
                text = path.read_text(encoding='utf-8-sig')
            except UnicodeError as exc:
                errors.append(f'{relative}: invalid UTF-8: {exc}')
                continue
            counts['utf8_text_files'] += 1
            if suffix not in ('.lua', '.yml'):
                errors.extend(f'{relative}: {e}' for e in brace_errors(text))
                counts['clausewitz_files'] += 1
            if path.is_relative_to(root / 'common/decisions') and path.read_bytes().startswith(b'\xef\xbb\xbf'):
                errors.append(f'{relative}: BOM in decision script')
            if suffix == '.yml':
                counts['localization_files'] += 1
                body = [s for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
                if not body:
                    counts['empty_or_comment_only_localization_overrides'] += 1
                    continue
                if not path.read_bytes().startswith(b'\xef\xbb\xbf'):
                    errors.append(f'{relative}: missing localization BOM')
                header = next((s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')), '')
                if not re.fullmatch(r'l_[a-z_]+:', header):
                    errors.append(f'{relative}: invalid localization header')
                elif header not in {f'l_{lang}:' for lang in ('english', 'russian', 'german', 'french', 'spanish', 'braz_por', 'polish', 'japanese', 'simp_chinese')}:
                    errors.append(f'{relative}: unsupported localization language {header}')
                for line, value in enumerate(text.splitlines(), 1):
                    if not value.strip() or value.lstrip().startswith('#') or re.fullmatch(r'\s*l_[a-z_]+:\s*', value):
                        continue
                    if not ENTRY.match(value):
                        errors.append(f'{relative}:{line}: invalid localization entry')
                    else:
                        counts['localization_entries'] += 1
        if suffix == '.dds':
            with path.open('rb') as stream:
                header = stream.read(128)
            if len(header) < 128 or header[:4] != b'DDS ' or struct.unpack_from('<I', header, 4)[0] != 124:
                # RT56 also ships PNG/JPEG/TGA data using .dds filenames.
                # Validate the actual encoding rather than rejecting by suffix.
                try:
                    from PIL import Image
                    with Image.open(path) as image:
                        if image.format not in ('PNG', 'JPEG', 'TGA'):
                            raise ValueError('Unsupported alternate texture format')
                        image.verify()
                    with Image.open(path) as image:
                        image.load()
                        counts['verified_alternate_texture_formats'] += 1
                except (ImportError, OSError, ValueError) as exc:
                    errors.append(f'{relative}: invalid texture data: {exc}')
            elif not all(struct.unpack_from('<II', header, 12)):
                errors.append(f'{relative}: invalid DDS dimensions')
            counts['dds_files'] += 1

    library = ctypes.util.find_library('lua5.4')
    if library:
        lua = ctypes.CDLL(library)
        lua.luaL_newstate.restype = ctypes.c_void_p
        lua.luaL_loadfilex.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lua.lua_tolstring.restype = ctypes.c_char_p
        lua.lua_close.argtypes = [ctypes.c_void_p]
        for path in root.rglob('*.lua'):
            state = lua.luaL_newstate()
            try:
                if lua.luaL_loadfilex(state, str(path).encode(), b't'):
                    errors.append(f'{path.relative_to(root)}: Lua syntax: {lua.lua_tolstring(state, -1, None).decode(errors="replace")}')
                counts['lua_files_compiled_without_execution'] += 1
            finally:
                lua.lua_close(state)
    else:
        warnings.append('Lua syntax check skipped: liblua5.4 is unavailable')

    conflicts, event_count = event_conflicts(root, None)
    counts['events'] = event_count
    errors.extend(f'Duplicate event ID: {c["id"]}' for c in conflicts)
    for category in CATEGORIES:
        records = definitions(root, category)
        counts[f'unique_{category}'] = len(records)
        for identity, items in records.items():
            if len(items) <= 1:
                continue
            message = f'Duplicate {category} ID {identity}: {[str(p.relative_to(root)) for p, _, _ in items]}'
            # The fork intentionally specializes 10 vanilla-style traits.
            if category == 'traits' and all('miyagipatch' not in p.name for p, _, _ in items):
                warnings.append(message)
            else:
                errors.append(message)
    focus_ids = definitions(root, 'focuses').keys()
    for path in (root / 'common/national_focus').rglob('*.txt'):
        for node in walk(parse(path.read_text(encoding='utf-8-sig'))):
            if node['key'] == 'prerequisite':
                for identity in node['values'].get('focus', []):
                    counts['focus_prerequisite_references'] += 1
                    if identity not in focus_ids:
                        errors.append(f'{path.name}: missing focus prerequisite {identity}')

    tech_ids = definitions(root, 'technologies').keys()
    unresolved = set()
    for path in (root / 'common/technologies').rglob('*.txt'):
        for node in walk(parse(path.read_text(encoding='utf-8-sig'))):
            for identity in node['values'].get('leads_to_tech', []):
                if identity not in tech_ids:
                    unresolved.add(identity)
    if any(k.startswith('r56rp_') for k in unresolved):
        errors.append(f'Undefined RP technology path targets: {sorted(unresolved)}')
    if unresolved:
        warnings.append(f'Technology targets requiring vanilla definitions or runtime verification: {sorted(unresolved)}')

    world = ProvinceMap(root)
    states = state_provinces(root, root)
    missing = sorted(set(range(1, max(states) + 1)) - states.keys())
    if missing:
        errors.append(f'Non-contiguous states: {missing}')
    owners = collections.defaultdict(list)
    for state, provinces in states.items():
        for province in provinces:
            owners[province].append(state)
            if province not in world.definitions:
                errors.append(f'State {state}: unknown province {province}')
    for province, state_ids in owners.items():
        if len(state_ids) != 1:
            errors.append(f'Province {province}: multiple states {state_ids}')
    for province, fields in world.definitions.items():
        if province != 0 and fields[4] == 'land' and province not in owners:
            errors.append(f'Unassigned land province {province}')
    for path in (root / 'history/states').glob('*.txt'):
        tree = parse(path.read_text(encoding='utf-8-sig'))
        state = next((n for n in tree['children'] if n['key'] == 'state'), None)
        if state:
            sid = int(state['values']['id'][0])
            for n in walk(state):
                if n['key'] == 'victory_points' and n['atoms']:
                    province = int(n['atoms'][0])
                    if province not in states[sid]:
                        errors.append(f'{path.name}: victory point {province} belongs to another state')
    counts['states'] = len(states)
    counts['province_definitions'] = len(world.definitions)
    counts['assigned_provinces'] = len(owners)
    ports = check_ports(root, root)
    sites = check_state_sites(root, root)
    errors.extend(f'Port placement: {p}' for p in ports['errors'])
    errors.extend(f'State building site: {p}' for p in sites['errors'])
    counts['naval_spawns_checked'] = ports['naval_spawns_checked']
    counts['state_sites_checked'] = sites['state_sites_checked']

    regions = collections.defaultdict(list)
    for path in (root / 'map/strategicregions').glob('*.txt'):
        for n in parse(path.read_text(encoding='utf-8-sig'))['children']:
            if n['key'] != 'strategic_region':
                continue
            region = n['values'].get('id', ['?'])[0]
            for p in n['children']:
                if p['key'] == 'provinces':
                    for v in p['atoms']:
                        regions[int(v)].append(region)
    for province in world.definitions:
        if province != 0 and len(regions[province]) != 1:
            errors.append(f'Province {province}: strategic region count {len(regions[province])}')
    counts['provinces_checked_in_strategic_regions'] = len(regions)

    for path in (root / 'common/country_tags').glob('*.txt'):
        for tag, value in re.findall(r'^\s*([A-Z0-9]{3})\s*=\s*"([^"]+)"', path.read_text(encoding='utf-8-sig'), re.M):
            counts['declared_country_tags'] += 1
            if not (root / 'common' / value).is_file():
                errors.append(f'{path.name}: country definition missing for {tag}: {value}')

    descriptor = root / 'descriptor.mod'
    text = descriptor.read_text() if descriptor.exists() else ''
    for required in ('supported_version="1.19.*"', 'replace_path="history/states"', 'replace_path="map/strategicregions"'):
        if required not in text:
            errors.append(f'Descriptor missing {required}')
    warnings.append('HOI4 runtime, vanilla files, DLC combinations and old saves are untested; the provided executable is not run.')
    if counts['verified_alternate_texture_formats']:
        warnings.append(f'{counts["verified_alternate_texture_formats"]} .dds-named textures contain valid PNG/JPEG/TGA data; bytes retained from source, engine rendering untested.')
    return {'static_checks_passed': not errors, 'counts': dict(counts), 'errors': errors,
            'limitations': warnings, 'runtime_tested': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = validate(args.root)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['static_checks_passed'] else 1)
