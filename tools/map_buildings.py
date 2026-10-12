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


def synchronize(root, base):
    """Use the installed map's building table, transferring only extra-state points."""
    world = ProvinceMap(base)
    base_states = state_provinces(base, base)
    current_states = state_provinces(root, base)
    owners = {pid: sid for sid, provinces in current_states.items() for pid in provinces}
    extra_states = current_states.keys() - base_states.keys()
    relocated, result = [], []
    for number, line, fields in rows(base / 'map/buildings.txt'):
        province = world.at(fields[2], fields[4])
        target_state = owners.get(province)
        if target_state in extra_states and int(fields[0]) != target_state:
            relocated.append({'line': number, 'province': province, 'from_state': int(fields[0]),
                              'to_state': target_state, 'building': fields[1]})
            fields[0] = str(target_state)
            line = ';'.join(fields)
        result.append(line)
    target = root / 'map/buildings.txt'
    target.write_text('\n'.join(result) + '\n', encoding='utf-8')
    return {'source': 'User-supplied The Road to 56 map/buildings.txt',
            'extra_states': sorted(extra_states), 'relocated_building_points': relocated,
            'building_records': len(result)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--synchronize', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = synchronize(args.root, args.base) if args.synchronize else check_ports(args.root, args.base)
    if args.synchronize:
        result['port_validation'] = check_ports(args.root, args.base)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    raise SystemExit(0 if result.get('static_checks_passed', result.get('port_validation', {}).get('static_checks_passed')) else 1)
