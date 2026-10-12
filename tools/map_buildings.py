#!/usr/bin/env python3
"""Read map pixels to validate naval spawns and preserve RP's extra state.

Only reads the province bitmap. Does not edit images or execute game code.
"""
import argparse
import collections
import json
import re
import struct
from pathlib import Path

PROVINCE_BUILDINGS = {'bunker', 'supply_node', 'special_project_facility_spawn'}
STATE_SITES = ('air_base', 'rocket_site_spawn', 'stronghold_network',
               'nuclear_reactor_spawn', 'synthetic_refinery', 'radar_station',
               'fuel_silo', 'steel_mill', 'aluminum_mill', 'rubber_refinery')


class ProvinceMap:
    def __init__(self, base):
        self.definitions = {}
        for line in (base / 'map/definition.csv').read_text(encoding='utf-8-sig').splitlines():
            if line and not line.lstrip().startswith('#'):
                fields = line.split(';')
                self.definitions[int(fields[0])] = fields
        self.colors = {tuple(map(int, fields[1:4])): pid for pid, fields in self.definitions.items()}
        self.bitmap = (base / 'map/provinces.bmp').read_bytes()
        if self.bitmap[:2] != b'BM':
            raise ValueError('Invalid province BMP signature')
        self.offset = struct.unpack_from('<I', self.bitmap, 10)[0]
        self.width, self.height = struct.unpack_from('<ii', self.bitmap, 18)
        bits = struct.unpack_from('<H', self.bitmap, 28)[0]
        compression = struct.unpack_from('<I', self.bitmap, 30)[0]
        if bits != 24 or compression != 0 or self.width <= 0 or not self.height:
            raise ValueError('Expected an uncompressed 24-bit province bitmap')
        self.stride = (self.width * 3 + 3) & ~3
        if self.offset + abs(self.height) * self.stride > len(self.bitmap):
            raise ValueError('Truncated province bitmap')

    def at(self, x, z):
        x, z = int(float(x)), int(float(z))
        if not 0 <= x < self.width or not 0 <= z < abs(self.height):
            raise ValueError(f'Building coordinates outside the map: {x}, {z}')
        row = z if self.height > 0 else -self.height - 1 - z
        offset = self.offset + row * self.stride + x * 3
        blue, green, red = self.bitmap[offset:offset + 3]
        return self.colors[(red, green, blue)]


def state_provinces(root, base):
    states = {}
    for directory in [base, root]:
        for path in (directory / 'history/states').glob('*.txt'):
            text = re.sub(r'#.*', '', path.read_text(encoding='utf-8-sig'))
            identity = re.search(r'\bid\s*=\s*(\d+)', text)
            provinces = re.search(r'\bprovinces\s*=\s*{([^}]+)}', text)
            if identity and provinces:
                states[int(identity[1])] = set(map(int, re.findall(r'\d+', provinces[1])))
    return states


def rows(path):
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split(';')
        if len(fields) != 7:
            raise ValueError(f'{path.name}:{number}: expected seven building fields')
        yield number, line, fields


def check_ports(root, base):
    world = ProvinceMap(base)
    states = state_provinces(root, base)
    owners = {province: state for state, provinces in states.items() for province in provinces}
    building_file = root / 'map/buildings.txt'
    if not building_file.exists():
        building_file = base / 'map/buildings.txt'
    errors, warnings, seen = [], [], collections.Counter()
    for number, line, fields in rows(building_file):
        if fields[1] != 'naval_base_spawn':
            continue
        state, sea = int(fields[0]), int(fields[6])
        try:
            province = world.at(fields[2], fields[4])
        except (ValueError, KeyError) as exc:
            errors.append({'line': number, 'error': str(exc)})
            continue
        seen[province] += 1
        if world.definitions[province][4] != 'land':
            errors.append({'line': number, 'province': province, 'error': 'Naval spawn is not on land'})
        if owners.get(province) != state:
            errors.append({'line': number, 'province': province, 'state': state,
                           'correct_state': owners.get(province), 'error': 'Naval spawn assigned to the wrong state'})
        if sea not in world.definitions or world.definitions[sea][4] != 'sea':
            errors.append({'line': number, 'province': province, 'sea': sea,
                           'error': 'Naval spawn must reference a nonzero sea province'})
    for province, count in seen.items():
        if count > 1:
            # Fractional coast coordinates can project to the same sampled pixel;
            # these duplicates also occur in the supplied, working base map.
            warnings.append({'province': province, 'note': f'{count} naval spawns project to the same sampled province'})
    return {'static_checks_passed': not errors, 'naval_spawns_checked': sum(seen.values()),
            'distinct_spawn_provinces': len(seen), 'errors': errors, 'warnings': warnings}


def check_state_sites(root, base):
    """Check the complete set of singleton state sites supplied by RT56."""
    world = ProvinceMap(base)
    states = state_provinces(root, base)
    owners = {province: state for state, provinces in states.items() for province in provinces}
    path = root / 'map/buildings.txt'
    if not path.exists():
        path = base / 'map/buildings.txt'
    sites, errors = collections.Counter(), []
    for number, _, fields in rows(path):
        if fields[1] not in STATE_SITES:
            continue
        state, kind = int(fields[0]), fields[1]
        sites[state, kind] += 1
        try:
            province = world.at(fields[2], fields[4])
        except (ValueError, KeyError) as exc:
            errors.append({'line': number, 'error': str(exc)})
            continue
        if world.definitions[province][4] != 'land' or owners.get(province) != state:
            errors.append({'line': number, 'state': state, 'building': kind,
                           'province': province, 'correct_state': owners.get(province),
                           'error': 'State building site is outside its land provinces'})
    for state in sorted(states):
        for kind in STATE_SITES:
            count = sites[state, kind]
            if count != 1:
                errors.append({'state': state, 'building': kind, 'count': count,
                               'error': 'Expected one RT56 building site per state'})
    return {'static_checks_passed': not errors, 'states_checked': len(states),
            'state_sites_checked': sum(sites.values()), 'errors': errors}


def synchronize(root, base, rp_buildings):
    """Transfer province sites; retain state sites and restore RP's extra state."""
    world = ProvinceMap(base)
    base_states = state_provinces(base, base)
    current_states = state_provinces(root, base)
    owners = {pid: sid for sid, provinces in current_states.items() for pid in provinces}
    extra_states = current_states.keys() - base_states.keys()
    source = list(rows(base / 'map/buildings.txt'))
    template = list(rows(rp_buildings))
    retained = {line for _, line, fields in source
                if owners.get(world.at(fields[2], fields[4])) == int(fields[0])}
    candidates = collections.defaultdict(list)
    for _, line, fields in template:
        if line not in retained and owners.get(world.at(fields[2], fields[4])) == int(fields[0]):
            candidates[int(fields[0]), fields[1]].append(line)
    relocated, repositioned, added, result = [], [], [], []
    for number, line, fields in source:
        province = world.at(fields[2], fields[4])
        target_state = owners.get(province)
        if target_state in extra_states and int(fields[0]) != target_state:
            if fields[1] in PROVINCE_BUILDINGS:
                relocated.append({'line': number, 'province': province, 'from_state': int(fields[0]),
                                  'to_state': target_state, 'building': fields[1]})
                fields[0] = str(target_state)
                line = ';'.join(fields)
            else:
                choices = candidates[int(fields[0]), fields[1]]
                if not choices:
                    raise ValueError(f'No valid RP replacement for state building: {line}')
                replacement = choices.pop(0)
                repositioned.append({'line': number, 'before': line, 'after': replacement})
                line = replacement
        result.append(line)
    for _, line, fields in template:
        if int(fields[0]) in extra_states and fields[1] not in PROVINCE_BUILDINGS:
            if owners.get(world.at(fields[2], fields[4])) != int(fields[0]):
                raise ValueError(f'RP extra-state site outside its state: {line}')
            added.append(line)
            result.append(line)
    target = root / 'map/buildings.txt'
    target.write_text('\n'.join(result) + '\n', encoding='utf-8')
    return {'source': 'User-supplied The Road to 56 map/buildings.txt',
            'extra_states': sorted(extra_states), 'relocated_building_points': relocated,
            'repositioned_source_state_points': repositioned,
            'added_extra_state_points': added,
            'building_records': len(result)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--synchronize', action='store_true')
    parser.add_argument('--rp-buildings', type=Path,
                        help='Original RP map/buildings.txt, needed when synchronizing extra states')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.synchronize and not args.rp_buildings:
        parser.error('--synchronize requires --rp-buildings')
    result = synchronize(args.root, args.base, args.rp_buildings) if args.synchronize else check_ports(args.root, args.base)
    if args.synchronize:
        result['port_validation'] = check_ports(args.root, args.base)
    result['state_site_validation'] = check_state_sites(args.root, args.base)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    valid = result.get('static_checks_passed', result.get('port_validation', {}).get('static_checks_passed'))
    raise SystemExit(0 if valid and result['state_site_validation']['static_checks_passed'] else 1)
