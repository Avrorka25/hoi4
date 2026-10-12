import re
from pathlib import Path

TOKEN = re.compile(r'#[^\n]*|"(?:\\.|[^"\\])*"|[{}=<>]|[^\s{}=<>#"]+')


def parse(text):
    tokens = [m for m in TOKEN.finditer(text) if not m.group().startswith('#')]
    def block(index, key='root', start=0):
        node = {'key': key, 'children': [], 'values': {}, 'atoms': [], 'start': start}
        while index < len(tokens):
            current = tokens[index]
            name = current.group().strip('"')
            if name == '}':
                node['end'] = current.start()
                return node, index + 1
            if index + 1 < len(tokens) and tokens[index + 1].group() == '=':
                if index + 2 >= len(tokens):
                    break
                value = tokens[index + 2]
                if value.group() == '{':
                    child, index = block(index + 3, name, value.end())
                    child['declaration_start'] = current.start()
                    node['children'].append(child)
                else:
                    node['values'].setdefault(name, []).append(value.group().strip('"'))
                    index += 3
            elif name == '{':
                child, index = block(index + 1, '', current.end())
                node['children'].append(child)
            else:
                node['atoms'].append(name)
                index += 1
        node['end'] = len(text)
        return node, index
    return block(0)[0]


def walk(node):
    yield node
    for child in node['children']:
        yield from walk(child)


def definitions(root, subdirectory, kind):
    result = {}
    for path in (root / subdirectory).glob('*.txt'):
        text = path.read_text(encoding='utf-8-sig', errors='replace')
        tree = parse(text)
        if kind == 'tech':
            ids = {n['key'] for top in tree['children'] if top['key'] == 'technologies' for n in top['children']}
        else:
            ids = {n['values']['id'][0] for n in walk(tree)
                   if n['key'] in ['focus', 'shared_focus'] and 'id' in n['values']}
        result[path.relative_to(root).as_posix().casefold()] = (path, ids)
    return result
