#!/usr/bin/env python3
"""Build standalone v2 Russian localisation from exact source/translation pairs.

The supplied RT56 translation is reused only for unchanged English text and
unchanged game expressions. Generated files retain the English file order and
replace/ directory. The offline model is optional; no game scripts are executed.
"""
import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

ENTRY = re.compile(r'^\s*([^\s:#]+):\d*\s*"(.*)"\s*(?:#.*)?$')
PROTECTED = re.compile(r'\[[^\]\n]*\]|\$[^$\n]*\$|£[A-Za-z_][A-Za-z_0-9]*(?:£|\|\d+)?|@[A-Za-z0-9_]+|§[A-Za-z0-9!]|\\"|\\[nrt\\]')
GAME = re.compile(r'\[[^\]\n]*\]|\$[^$\n]*\$|£[A-Za-z_][A-Za-z_0-9]*(?:£|\|\d+)?|@[A-Za-z0-9_]+|§[A-Za-z0-9!]|\\[nrt]')
LATIN = re.compile(r'[A-Za-z]')
PACKAGE_VERSION = '1.19-v2fix.2-ru'
LANGUAGES = {'english', 'russian', 'french', 'german', 'spanish', 'braz_por', 'polish', 'japanese', 'simp_chinese'}
SOURCE_REPAIRS = [
    ('r56_countries_cosmetic_l_english.yml', 'CYP_UK_autonomy_integrated_puppet:0 "$$OVERLORDADJ$ $NONIDEOLOGY$"', 'CYP_UK_autonomy_integrated_puppet:0 "$OVERLORDADJ$ $NONIDEOLOGY$"'),
    ('FIN_l_english.yml', '"[SOV.GetName  Agrees."', '"[SOV.GetName] Agrees."'),
    ('GRE_l_english.yml', '[GRE.GetnameDef]]', '[GRE.GetNameDef]'),
    ('IRQ_l_english.yml', '[IRQ.GetnameDefCap has', '[IRQ.GetNameDefCap] has'),
]


def entries(path, strict=True):
    result = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#') or re.fullmatch(r'\s*l_[a-z_]+:\d*\s*', line):
            continue
        match = ENTRY.match(line)
        if not match:
            if not strict:
                print(f'Skipped malformed reference line: {path.name}:{number}', flush=True)
                continue
            raise ValueError(f'{path}:{number}: invalid localisation')
        result[match[1]] = match[2]
    return result


def catalog(directory, strict=True):
    return {p.relative_to(directory).as_posix(): entries(p, strict) for p in sorted(directory.rglob('*.yml'))}


def header(path):
    return next((s.strip() for s in path.read_text(encoding='utf-8-sig').splitlines()
                 if s.strip() and not s.lstrip().startswith('#')), '')


def english_paths(root):
    directory = root / 'localisation'
    result = {}
    for path in sorted(directory.rglob('*.yml')):
        if path.is_relative_to(directory / 'english'):
            name = path.relative_to(directory / 'english').as_posix()
        elif header(path) == 'l_english:':
            name = '__external__/' + path.relative_to(directory).as_posix()
        else:
            continue
        result[name] = path
    return result


def english_catalog(root):
    return {name: entries(path) for name, path in english_paths(root).items()}


def target_path(root, name):
    if name.startswith('__external__/'):
        return root / 'localisation' / name.removeprefix('__external__/')
    return root / 'localisation/russian' / name


def russian_catalog(root):
    result = catalog(root / 'localisation/russian')
    for path in sorted((root / 'localisation').rglob('*.yml')):
        if not path.is_relative_to(root / 'localisation/russian') and header(path) == 'l_russian:':
            result['__external__/' + path.relative_to(root / 'localisation').as_posix()] = entries(path)
    return result


def safe(source, target):
    return isinstance(target, str) and collections.Counter(GAME.findall(source)) == collections.Counter(GAME.findall(target))


def quote(value):
    return re.sub(r'(?<!\\)"', r'\"', value).replace('\r', '').replace('\n', r'\n')


def russian_name(relative):
    path = Path(relative)
    name = path.name.replace('_l_english', '_l_russian')
    if name == path.name:
        name = path.stem.removesuffix('_english') + '_l_russian.yml'
    return (path.parent / name).as_posix()


def pairs(english, russian):
    flat = {k: v for file in russian.values() for k, v in file.items()}
    result = {}
    for name, values in english.items():
        preferred = russian.get(russian_name(name), {})
        for key, source in values.items():
            target = preferred.get(key, flat.get(key))
            if target is not None and safe(source, target):
                result[(key, source)] = target
    return result


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def prepare(args):
    for name, before, after in SOURCE_REPAIRS:
        path = args.root / 'localisation/english' / name
        text = path.read_bytes().decode('utf-8')
        if before in text:
            path.write_bytes(text.replace(before, after).encode('utf-8'))
    source = english_catalog(args.root)
    baseline = pairs(catalog(args.base / 'localisation/english', False), catalog(args.russian, False))
    previous = pairs(catalog(args.old / 'localisation/english'), catalog(args.old / 'localisation/russian')) if args.old else {}
    memory = json.loads(args.memory.read_text()) if args.memory else {}
    records, counts = [], collections.Counter()
    for name, values in source.items():
        for key, value in values.items():
            pair = (key, value)
            target, method = None, None
            if pair in baseline:
                target, method = baseline[pair], 'supplied_rt56_rus'
            elif pair in previous:
                target, method = previous[pair], 'previous_rp_translation'
            elif value in memory and safe(value, memory[value]):
                target, method = memory[value], 'existing_machine_memory'
            elif not LATIN.search(PROTECTED.sub('', value)):
                target, method = value, 'expression_or_number'
            # Empty text and historical designations need no prose translation.
            if target is not None:
                counts[method] += 1
            else:
                counts['pending'] += 1
            records.append({'file': name, 'key': key, 'english': value, 'russian': target, 'method': method})
    dump(args.work / 'records.json', records)
    dump(args.work / 'inventory.json', {'files': len(source), 'entries': len(records), 'keys': len({r['key'] for r in records}), 'methods': counts})
    print(json.dumps({'files': len(source), 'entries': len(records), 'methods': counts}, ensure_ascii=False), flush=True)


def split(value):
    result, cursor = [], 0
    for match in PROTECTED.finditer(value):
        result.append((False, value[cursor:match.start()]))
        result.append((True, match[0]))
        cursor = match.end()
    result.append((False, value[cursor:]))
    prepared = []
    for protected, text in result:
        if protected or not LATIN.search(text):
            prepared.append((True, text))
            continue
        for fragment in re.split(r'(?<=[.!?;])(?=\s)', text):
            leading = fragment[:len(fragment) - len(fragment.lstrip())]
            trailing = fragment[len(fragment.rstrip()):]
            content = fragment.strip()
            if content:
                prepared.extend([(True, leading), (False, content), (True, trailing)])
    return prepared


def translate(args):
    import ctranslate2
    import sentencepiece
    model = args.model / 'model/model.bin'
    tokenizer = args.model / 'sentencepiece.model'
    for path, expected in [(model, 'c262cce6a506447d4b3c7a899787694df13783340ba67f0df3ecbf046ae27924'),
                           (tokenizer, 'cdd7fd3f6b069cd1baa90d95682ec6851119ae0d734438ba85d3678b2f1e2a66')]:
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError(f'Pinned model checksum mismatch: {path.name}')
    records = json.loads((args.work / 'records.json').read_text())
    cache_path = args.work / 'segments.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    if args.segments:
        cache = {**json.loads(args.segments.read_text()), **cache}
    pending = {r['english']: split(r['english']) for r in records if r['russian'] is None}
    sp = sentencepiece.SentencePieceProcessor(model_file=str(tokenizer))
    units = {text for pieces in pending.values() for protected, text in pieces if not protected and text not in cache}
    encoded = {text: sp.encode(text, out_type=str) for text in units}
    if any(len(tokens) > 1000 for tokens in encoded.values()):
        raise ValueError('Sentence exceeds model limit; split before translating')
    contents = sorted(units, key=lambda text: len(encoded[text]))
    engine = ctranslate2.Translator(str(model.parent), device='cpu', compute_type='int8', inter_threads=1, intra_threads=args.threads)
    print(f'Offline translation: {len(pending)} values, {len(contents)} uncached segments', flush=True)
    for index in range(0, len(contents), 64):
        batch = contents[index:index + 64]
        limit = min(1200, max(len(encoded[s]) for s in batch) * 3 + 16)
        translated = engine.translate_batch([encoded[s] for s in batch], beam_size=2, max_input_length=1024,
                                            max_decoding_length=limit, repetition_penalty=1.05,
                                            no_repeat_ngram_size=4)
        cache.update({text: sp.decode(result.hypotheses[0]) for text, result in zip(batch, translated)})
        if index % 320 == 0:
            dump(cache_path, cache)
            print(f'Segments: {min(index + 64, len(contents))}/{len(contents)}', flush=True)
    dump(cache_path, cache)
    for record in records:
        if record['russian'] is None:
            value = ''.join(text if protected else cache[text] for protected, text in pending[record['english']])
            if not safe(record['english'], value):
                raise ValueError(f'Game expressions changed: {record["file"]}:{record["key"]}')
            record.update(russian=value, method='offline_machine_translation')
    dump(args.work / 'records.json', records)
    print('All records translated.', flush=True)


def expand(args):
    """Retain all original lines, including repeated keys and version numbers."""
    records = json.loads((args.work / 'records.json').read_text())
    exact = {(r['file'], r['key'], r['english']): r for r in records}
    values = collections.defaultdict(set)
    for record in records:
        if record['russian'] is not None:
            values[record['english']].add(record['russian'])
    segments = json.loads((args.work / 'segments.json').read_text())
    expanded = []
    for name, path in english_paths(args.root).items():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            match = ENTRY.match(line)
            if not match:
                continue
            key, value = match[1], match[2]
            original = exact.get((name, key, value))
            if original:
                record = original.copy()
            else:
                translated, method = None, None
                if len(values[value]) == 1:
                    translated, method = next(iter(values[value])), 'sourced_exact_text'
                else:
                    pieces = split(value)
                    if all(protected or text in segments for protected, text in pieces):
                        translated = ''.join(text if protected else segments[text] for protected, text in pieces)
                        method = 'offline_machine_translation'
                if translated is not None and not safe(value, translated):
                    translated, method = None, None
                record = {'file': name, 'key': key, 'english': value, 'russian': translated, 'method': method}
            record['version'] = re.search(r':(\d*)\s*"', line)[1] or '0'
            expanded.append(record)
    dump(args.work / 'records.json', expanded)
    print(f'Restored original localisation line order: {len(expanded)} lines; {sum(r["russian"] is None for r in expanded)} require translation', flush=True)


def build(args):
    records = json.loads((args.work / 'records.json').read_text())
    overrides = json.loads(args.overrides.read_text()) if args.overrides else {}
    tools = Path(__file__).resolve().parent
    terms = json.loads((tools / 'v2_terms_ru.json').read_text())
    latin = json.loads((tools / 'v2_latin_terms_ru.json').read_text())
    latin_pattern = re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(re.escape(k) for k in sorted(latin, key=len, reverse=True)) + r')(?![A-Za-z0-9_])', re.I)
    latin = {k.casefold(): v for k, v in latin.items()}
    reuse = json.loads((args.work / 'reuse.json').read_text()) if (args.work / 'reuse.json').exists() else {}
    segments = json.loads((args.work / 'segments.json').read_text())
    names = {r['english']: r['russian'] for r in records if r['russian'] and r['russian'] != r['english']
             and r['method'] in ('supplied_rt56_rus', 'previous_rp_translation')}
    names.update(json.loads((tools / 'v2_formable_names_ru.json').read_text()))
    files, methods = collections.defaultdict(list), collections.Counter()
    for record in records:
        key, original, translated = record['key'], record['english'], record['russian']
        method = record['method']
        if method == 'offline_machine_translation' and original in reuse and safe(original, reuse[original]):
            translated, method = reuse[original], 'sourced_exact_text'
        pieces = split(original)
        if any(not protected for protected, _ in pieces) and all(protected or text in terms for protected, text in pieces):
            translated = ''.join(text if protected else terms[text] for protected, text in pieces)
            method = 'reviewed_terms'
        elif (method == 'offline_machine_translation'
              and any(not protected and text in terms for protected, text in pieces)
              and all(protected or text in terms or text in segments for protected, text in pieces)):
            translated = ''.join(text if protected else terms.get(text, segments[text]) for protected, text in pieces)
            method = 'machine_with_reviewed_terms'
        match = re.fullmatch(r'SPG Buff ([IVX]+)', original)
        if match:
            translated, method = 'Улучшение самоходной артиллерии ' + match[1], 'reviewed_terms'
        match = re.fullmatch(r'Support (Light|Medium|Heavy|Super-Heavy|Modern) (SPAA|SPG|TD)', original)
        if match:
            tier = {'Light': 'Лёгкие', 'Medium': 'Средние', 'Heavy': 'Тяжёлые', 'Super-Heavy': 'Сверхтяжёлые', 'Modern': 'Современные'}[match[1]]
            kind = {'SPAA': 'ЗСУ', 'SPG': 'САУ', 'TD': 'ПТ-САУ'}[match[2]]
            translated, method = f'{tier} {kind} поддержки', 'reviewed_terms'
        match = re.fullmatch(r'(Show|Hide) (.+?)(?: Formable)?', original)
        if match and record['file'] in ('r56rp_formables_showhide_l_english.yml', 'rp_form_nation_l_english.yml') and (match[2] in names or match[2] in reuse):
            translated = ('Показать создание: ' if match[1] == 'Show' else 'Скрыть создание: ') + names.get(match[2], reuse.get(match[2]))
            method = 'reviewed_template_with_sourced_name'
        if key in overrides:
            translated = overrides[key]
            method = 'reviewed_override'
        if re.fullmatch(r'GFX_[A-Za-z0-9_]+', original):
            translated, method = original, 'functional_identifier'
        elif not LATIN.search(PROTECTED.sub('', original)) and not safe(original, translated):
            translated, method = original, 'expression_or_number'
        if method in ('offline_machine_translation', 'machine_with_reviewed_terms', 'existing_machine_memory') and translated:
            # Only fix untranslated terminology in free text, outside game tokens.
            chunks, cursor = [], 0
            for protected in PROTECTED.finditer(translated):
                chunks.append(latin_pattern.sub(lambda m: latin[m[0].casefold()], translated[cursor:protected.start()]))
                chunks.append(protected[0])
                cursor = protected.end()
            chunks.append(latin_pattern.sub(lambda m: latin[m[0].casefold()], translated[cursor:]))
            output = ''.join(chunks)
            if output != translated:
                translated, method = output, 'machine_with_reviewed_terms'
        if ('machine' in method and not re.search(r'[А-Яа-яЁё]', PROTECTED.sub('', translated))
                and re.search(r'equipment|support_weapons|modules', key)):
            # A model must not invent characters/numbers in untranslated model names.
            translated, method = original, 'historical_designation'
        if translated is None or not safe(original, translated):
            raise ValueError(f'Incomplete or changed game expressions: {record["file"]}:{key}')
        record.update(russian=translated, method=method)
        files[russian_name(record['file'])].append((key, translated, record.get('version', '0')))
        methods[record['method']] += 1
    # Mirror comment-only files as well; they suppress inherited overrides.
    for relative in english_catalog(args.root):
        files.setdefault(russian_name(relative), [])
    for name, values in files.items():
        target = target_path(args.root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = 'l_russian:\n' + ''.join(f' {k}:{version} "{quote(v)}"\n' for k, v, version in values)
        target.write_text(body, encoding='utf-8-sig')
    dump(args.work / 'records.json', records)
    dump(args.work / 'coverage.json', {'russian_files': len(files), 'covered_entries': len(records),
                                     'unique_keys': len({r['key'] for r in records}), 'pending_entries': 0,
                                     'methods': methods, 'game_expressions_preserved': True})
    for descriptor in (args.root / 'descriptor.mod', args.root.parent / (args.root.name + '.mod')):
        if descriptor.exists():
            text = descriptor.read_text(encoding='utf-8')
            descriptor.write_text(re.sub(r'^version="[^"]*"', f'version="{PACKAGE_VERSION}"', text, flags=re.M), encoding='utf-8')
    print(json.dumps({'files': len(files), 'entries': len(records), 'methods': methods}, ensure_ascii=False), flush=True)


def interface(args):
    mapping = json.loads(args.overrides.read_text())
    keys = {value: 'V2_RU_INTERFACE_' + hashlib.sha256(value.encode()).hexdigest()[:12].upper() for value in mapping}
    pattern = re.compile(r'(\b(?:text|pdx_tooltip|tooltip)\s*=\s*")([^"\n]+)(")')
    changes = []
    for path in sorted((args.root / 'interface').rglob('*.gui')):
        text = path.read_text(encoding='utf-8-sig')
        def replace(match):
            line_start = text.rfind('\n', 0, match.start()) + 1
            if '#' in text[line_start:match.start()] or match[2] not in keys:
                return match[0]
            changes.append({'file': path.relative_to(args.root).as_posix(), 'english': match[2], 'key': keys[match[2]]})
            return match[1] + keys[match[2]] + match[3]
        output = pattern.sub(replace, text)
        if output != text:
            # Preserve the original line ending and BOM; only text values change.
            raw = path.read_bytes()
            if b'\r\n' in raw:
                output = output.replace('\n', '\r\n')
            path.write_bytes((b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + output.encode())
    used = {x['english'] for x in changes}
    records = json.loads((args.work / 'records.json').read_text())
    filename = 'zzz_v2_interface_l_english.yml'
    for value in sorted(used):
        if not safe(value, mapping[value]):
            raise ValueError('Interface game expressions changed')
        records.append({'file': filename, 'key': keys[value], 'english': value,
                        'russian': mapping[value], 'method': 'reviewed_interface'})
    path = args.root / 'localisation/english' / filename
    if used:
        path.write_text('l_english:\n' + ''.join(f' {keys[v]}:0 "{quote(v)}"\n' for v in sorted(used)), encoding='utf-8-sig')
        for directory in (args.root / 'localisation').iterdir():
            if directory.is_dir() and directory.name in LANGUAGES - {'english', 'russian'}:
                fallback = directory / f'zzz_v2_interface_l_{directory.name}.yml'
                fallback.write_text(f'l_{directory.name}:\n' + ''.join(f' {keys[v]}:0 "{quote(v)}"\n' for v in sorted(used)), encoding='utf-8-sig')
    dump(args.work / 'records.json', records)
    dump(args.work / 'interface.json', {'replaced_text_values': len(changes), 'localisation_keys': len(used), 'changes': changes})
    print(f'Localised {len(changes)} hardcoded interface texts ({len(used)} unique strings)', flush=True)


def factions(args):
    mapping = json.loads(args.overrides.read_text())
    keys = {value: 'V2_RU_FACTION_' + hashlib.sha256(value.encode()).hexdigest()[:12].upper() for value in mapping}
    pattern = re.compile(r'(\b(?:create_faction|set_faction_name)\s*=\s*")([^"\n]+)(")')
    changes, verified = [], []
    for directory in ('common/national_focus', 'events', 'common/decisions', 'common/scripted_effects'):
        for path in sorted((args.root / directory).rglob('*.txt')):
            raw = path.read_bytes()
            text = raw.decode('utf-8')
            def replace(match):
                start = text.rfind('\n', 0, match.start()) + 1
                if '#' in text[start:match.start()] or match[2] not in keys:
                    return match[0]
                changes.append({'file': path.relative_to(args.root).as_posix(), 'english': match[2], 'key': keys[match[2]]})
                return match[1] + keys[match[2]] + match[3]
            output = pattern.sub(replace, text)
            if output != text:
                # Reversing only the new display-name keys must recover every byte.
                inverse = {v: k for k, v in keys.items()}
                reverted = pattern.sub(lambda m: m[1] + inverse.get(m[2], m[2]) + m[3], output)
                if reverted.encode('utf-8') != raw:
                    raise ValueError(f'Unexpected scenario change: {path}')
                path.write_bytes(output.encode('utf-8'))
                verified.append(path.relative_to(args.root).as_posix())
    used = {x['english'] for x in changes}
    records = json.loads((args.work / 'records.json').read_text())
    for value in sorted(used):
        records.append({'file': 'zzz_v2_factions_l_english.yml', 'key': keys[value], 'english': value,
                        'russian': mapping[value], 'method': 'reviewed_faction_name'})
    for directory in (args.root / 'localisation').iterdir():
        if directory.is_dir() and directory.name in LANGUAGES - {'russian'}:
            path = directory / f'zzz_v2_factions_l_{directory.name}.yml'
            path.write_text(f'l_{directory.name}:\n' + ''.join(f' {keys[v]}:0 "{quote(v)}"\n' for v in sorted(used)), encoding='utf-8-sig')
    dump(args.work / 'records.json', records)
    dump(args.work / 'factions.json', {'changed_files': verified, 'changed_display_names': len(changes),
                                     'unique_faction_names': len(used), 'only_faction_display_names_changed': True,
                                     'changes': changes})
    print(f'Localised {len(changes)} faction display names ({len(used)} unique strings)', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'translate', 'build', 'interface', 'factions', 'expand'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--russian', type=Path)
    parser.add_argument('--old', type=Path)
    parser.add_argument('--memory', type=Path)
    parser.add_argument('--segments', type=Path)
    parser.add_argument('--overrides', type=Path)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    {'prepare': prepare, 'translate': translate, 'build': build, 'interface': interface, 'factions': factions, 'expand': expand}[args.command](args)
