#!/usr/bin/env python3
"""Static validation; this does not run or emulate Hearts of Iron IV."""
from __future__ import annotations

import argparse
import collections
import ctypes
import ctypes.util
import json
import re
import struct
from pathlib import Path

REFERENCES = re.compile(r'\[[^\]\n]*\]|\$[A-Za-z_?][^$\n]*\$|£[A-Za-z_][A-Za-z_0-9]*(?:£|\|\d+)?')
ENTRY = re.compile(r'^\s*([^\s:#]+):\d*\s*"(.*)"\s*(?:#.*)?$')


def entries(path: Path) -> dict[str, str]:
    result = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#') or line.strip() in ('l_english:', 'l_russian:'):
            continue
        match = ENTRY.match(line)
        if match is None:
            raise ValueError(f'{path}:{number}: invalid localization entry')
        result[match[1]] = match[2]
    return result


def event_ids(text: str) -> list[tuple[str, int]]:
    """Read event declarations, ignoring comments, strings and nested effect IDs.

    Engine IDs use a numeric suffix, so namespace.01 and namespace.1 collide.
    Files at the same relative path override the base file before this check.
    """
    tokens = list(re.finditer(r'#[^\n]*|"(?:\\.|[^"\\])*"|[{}=]|[^\s{}=#"]+', text))
    depth = 0
    event_depth = None
    result = []
    previous = []
    for match in tokens:
        token = match.group()
        if token.startswith('#'):
            continue
        if token == '{':
            if depth == 0 and previous[-2:] in (["country_event", "="], ["news_event", "="],
                                               ["state_event", "="], ["unit_leader_event", "="]):
                event_depth = 1
            depth += 1
        elif token == '}':
            depth -= 1
            if depth == 0:
                event_depth = None
        elif depth == event_depth and previous[-2:] == ['id', '=']:
            value = token.strip('"')
            identity = re.fullmatch(r'([\w]+)\.(\d+)', value)
            if identity:
                result.append((f'{identity[1]}.{int(identity[2])}', text.count('\n', 0, match.start()) + 1))
        previous.append(token)
    return result


def event_conflicts(root: Path, base: Path | None) -> tuple[list[dict], int]:
    effective = {}
    for directory in ([base, root] if base else [root]):
        for path in (directory / 'events').rglob('*.txt'):
            effective[str(path.relative_to(directory))] = path
    owners = collections.defaultdict(list)
    for relative, path in effective.items():
        for identity, line in event_ids(path.read_text(encoding='utf-8-sig')):
            owners[identity].append({'file': relative, 'line': line, 'addon': path.is_relative_to(root)})
    return ([{'id': identity, 'declarations': paths} for identity, paths in sorted(owners.items())
             if len(paths) > 1], sum(map(len, owners.values())))


def brace_errors(text: str) -> list[str]:
    stack = []
    quote = escape = comment = False
    line = 1
    errors = []
    for char in text:
        if char == "\n":
            line += 1
            comment = False
        if comment:
            continue
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quote = False
            continue
        if char == "#":
            comment = True
        elif char == '"':
            quote = True
        elif char == "{":
            stack.append(line)
        elif char == "}":
            if not stack:
                errors.append(f"unmatched closing brace at {line}")
            else:
                stack.pop()
    if quote:
        errors.append("unterminated string")
    if stack:
        errors.append(f"unclosed braces at {stack}")
    return errors


def validate(root: Path, base: Path | None = None) -> dict:
    errors, warnings = [], []
    counts = collections.Counter()
    conflicts, total_events = event_conflicts(root, base)
    counts['event_declarations_with_reference_base' if base else 'event_declarations'] = total_events
    for conflict in conflicts:
        if any(declaration['addon'] for declaration in conflict['declarations']):
            errors.append(f"Duplicate event ID {conflict['id']}: {conflict['declarations']}")
        else:
            warnings.append(f"Reference base has duplicate event ID {conflict['id']}")
    for path in (root / 'common/decisions').glob('*.txt'):
        if path.read_bytes().startswith(b'\xef\xbb\xbf'):
            errors.append(f'{path.relative_to(root)}: BOM in decision script can corrupt the first category name')
    lua_library = ctypes.util.find_library("lua5.4")
    lua = ctypes.CDLL(lua_library) if lua_library else None
    if lua:
        lua.luaL_newstate.restype = ctypes.c_void_p
        lua.luaL_loadfilex.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lua.luaL_loadfilex.restype = ctypes.c_int
        lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        lua.lua_tolstring.restype = ctypes.c_char_p
        lua.lua_close.argtypes = [ctypes.c_void_p]
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        relative = str(p.relative_to(root))
        if p.suffix in (".txt", ".gui", ".gfx", ".asset", ".mod", ".lua"):
            try:
                text = p.read_text(encoding="utf-8-sig")
            except UnicodeError as exc:
                errors.append(f"{relative}: invalid UTF-8: {exc}")
                continue
            counts["utf8_text_files"] += 1
            if p.suffix != ".lua":
                for message in brace_errors(text):
                    errors.append(f"{relative}: {message}")
                counts["clausewitz_files"] += 1
            elif lua:
                state = lua.luaL_newstate()
                try:
                    if lua.luaL_loadfilex(state, str(p).encode(), b"t"):
                        message = lua.lua_tolstring(state, -1, None).decode("utf-8", errors="replace")
                        errors.append(f"{relative}: Lua syntax: {message}")
                    counts["lua_syntax_files"] += 1
                finally:
                    lua.lua_close(state)
        elif p.suffix == ".dds":
            data = p.read_bytes()
            if len(data) < 128 or data[:4] != b"DDS " or struct.unpack_from("<I", data, 4)[0] != 124:
                errors.append(f"{relative}: invalid DDS header")
            else:
                height, width = struct.unpack_from("<II", data, 12)
                if not height or not width:
                    errors.append(f"{relative}: zero-sized DDS texture")
            counts["dds_headers"] += 1
    for p in (root / "localisation").rglob("*.yml"):
        try:
            pairs = entries(p)
        except (UnicodeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        counts["localisation_files"] += 1
        counts["localisation_entries"] += len(pairs)
        if not p.read_bytes().startswith(b"\xef\xbb\xbf"):
            errors.append(f"{p.relative_to(root)}: missing UTF-8 BOM")
        if "_l_russian" in p.name:
            if p.read_text(encoding="utf-8-sig").splitlines()[0] != "l_russian:":
                errors.append(f"{p.name}: invalid Russian header")
            original = root / "localisation/english" / p.relative_to(root / "localisation/russian")
            original = original.with_name(original.name.replace("_l_russian", "_l_english"))
            if original.exists():
                source = entries(original)
                if "replace" not in p.parts and set(source) != set(pairs):
                    errors.append(f"{p.name}: missing or extra Russian keys")
                for key, value in pairs.items():
                    if key not in source:
                        errors.append(f"{p.name}:{key}: unknown source key")
                    elif collections.Counter(REFERENCES.findall(value)) != collections.Counter(REFERENCES.findall(source[key])):
                        errors.append(f"{p.name}:{key}: changed game references")
                counts["russian_entries"] += len(pairs)
    tags = {}
    for p in (root / "common/country_tags").glob("*.txt"):
        for tag, target in re.findall(r'^\s*([A-Z]{3})\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8-sig"), re.M):
            if tag in tags:
                errors.append(f"Duplicate country tag: {tag}")
            tags[tag] = target
            if not (root / "common" / target).is_file():
                errors.append(f"{tag}: missing country definition {target}")
            if not any((root / "history/countries").glob(f"{tag} -*.txt")):
                errors.append(f"{tag}: missing country history")
    counts["country_tags"] = len(tags)
    states = {}
    for directory in ([base, root] if base else [root]):
        for p in (directory / "history/states").glob("*.txt"):
            text = re.sub(r"#.*", "", p.read_text(encoding="utf-8-sig"))
            match = re.search(r"\bid\s*=\s*(\d+)", text)
            if match:
                states[int(match[1])] = (p, text)
    province_states = collections.defaultdict(set)
    for state, (p, text) in states.items():
        match = re.search(r"\bprovinces\s*=\s*{([^}]+)}", text)
        if not match:
            errors.append(f"{p.name}: missing provinces")
            continue
        provinces = set(re.findall(r"\b\d+\b", match[1]))
        for province in provinces:
            province_states[province].add(state)
        if p.is_relative_to(root):
            for victory_point in re.findall(r"\bvictory_points\s*=\s*{\s*(\d+)\s+\d+", text):
                if victory_point not in provinces:
                    errors.append(f"{p.name}: victory point {victory_point} does not belong to state {state}")
    for province, owners in province_states.items():
        if len(owners) > 1:
            errors.append(f"Province {province} assigned to states {sorted(owners)}")
    if base:
        missing = sorted(set(range(1, max(states) + 1)) - states.keys())
        if missing:
            errors.append(f"Non-contiguous state IDs with selected RT56 base: {missing}")
        definition = base / "map/definition.csv"
        if definition.exists():
            province_ids = {line.split(";")[0] for line in definition.read_text(encoding="utf-8-sig").splitlines()}
            for province in province_states:
                if province not in province_ids:
                    errors.append(f"Unknown province in selected RT56 map: {province}")
        counts["states_with_reference_base"] = len(states)
        counts["provinces_with_reference_base"] = len(province_states)
    descriptor = root / "descriptor.mod"
    if not descriptor.is_file():
        errors.append("Missing internal descriptor.mod")
    else:
        text = descriptor.read_text()
        if 'supported_version="1.19.*"' not in text:
            errors.append("Descriptor does not target HOI4 1.19")
        if '"The Road to 56 [Beta]"' in text:
            errors.append("Descriptor declares simultaneous stable and beta dependencies")
    warnings.append("Game runtime, actual installed RT56 version, DLC and saved games are untested.")
    if not lua:
        warnings.append("Lua compiler unavailable: Lua syntax was not checked.")
    return {"static_checks_passed": not errors, "counts": dict(counts), "errors": errors, "limitations": warnings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate(args.root, args.base)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["static_checks_passed"] else 1)
