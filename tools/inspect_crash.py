#!/usr/bin/env python3
"""Read crash registers and MSVC RTTI. Never load or execute the game binary.

An exact matching PE image can supply read-only metadata omitted by a minidump.
Raw memory, paths from module records, and user data are not printed.
"""
import argparse
import json
import struct
from pathlib import Path


class PEImage:
    def __init__(self, path):
        self.data = path.read_bytes()
        if self.data[:2] != b'MZ':
            raise ValueError('Not a PE executable')
        nt = struct.unpack_from('<I', self.data, 0x3c)[0]
        if self.data[nt:nt + 4] != b'PE\0\0':
            raise ValueError('Invalid PE signature')
        sections = struct.unpack_from('<H', self.data, nt + 6)[0]
        self.timestamp = struct.unpack_from('<I', self.data, nt + 8)[0]
        optional_size = struct.unpack_from('<H', self.data, nt + 20)[0]
        optional = nt + 24
        if struct.unpack_from('<H', self.data, optional)[0] != 0x20b:
            raise ValueError('Expected a 64-bit PE image')
        self.preferred_base = struct.unpack_from('<Q', self.data, optional + 24)[0]
        self.size, self.headers = struct.unpack_from('<II', self.data, optional + 56)
        self.sections = [struct.unpack_from('<IIII', self.data, optional + optional_size + 40 * i + 8)
                         for i in range(sections)]

    def read(self, rva, length):
        if rva < 0 or length < 0:
            return None
        if rva + length <= self.headers:
            return self.data[rva:rva + length]
        for virtual_size, start, raw_size, raw_offset in self.sections:
            if start <= rva and rva + length <= start + raw_size:
                offset = raw_offset + rva - start
                if offset + length <= len(self.data):
                    return self.data[offset:offset + length]
        return None


class Minidump:
    def __init__(self, path, image=None):
        self.data = path.read_bytes()
        if self.data[:4] != b'MDMP':
            raise ValueError('Not a minidump')
        count, directory = struct.unpack_from('<II', self.data, 8)
        self.streams = {t: (size, rva) for t, size, rva in
                        (struct.unpack_from('<III', self.data, directory + 12 * i) for i in range(count))}
        self.regions = []
        if 5 in self.streams:  # MemoryListStream, not ModuleListStream (4).
            _, rva = self.streams[5]
            count = struct.unpack_from('<I', self.data, rva)[0]
            self.regions.extend(struct.unpack_from('<QII', self.data, rva + 4 + 16 * i) for i in range(count))
        if 9 in self.streams:
            _, rva = self.streams[9]
            count, offset = struct.unpack_from('<QQ', self.data, rva)
            for i in range(count):
                start, size = struct.unpack_from('<QQ', self.data, rva + 16 + 16 * i)
                self.regions.append((start, size, offset))
                offset += size
        _, exception = self.streams[6]
        self.code, self.flags, self.record, self.address, parameters, _ = struct.unpack_from('<IIQQII', self.data, exception + 8)
        self.parameters = struct.unpack_from('<15Q', self.data, exception + 40)[:parameters]
        _, context = struct.unpack_from('<II', self.data, exception + 160)
        self.registers = {name: struct.unpack_from('<Q', self.data, context + offset)[0]
                          for name, offset in {'rax': 120, 'rcx': 128, 'rdx': 136, 'rbx': 144,
                                               'rsp': 152, 'rdi': 176, 'r12': 216,
                                               'r14': 232, 'r15': 240, 'rip': 248}.items()}
        _, modules = self.streams[4]
        count = struct.unpack_from('<I', self.data, modules)[0]
        for i in range(count):
            base, size, _, timestamp, _ = struct.unpack_from('<QIIII', self.data, modules + 4 + 108 * i)
            if base <= self.address < base + size:
                self.base, self.size, self.timestamp = base, size, timestamp
                break
        else:
            raise ValueError('Fault module not found')
        self.image = image
        if image and (image.size != self.size or image.timestamp != self.timestamp):
            raise ValueError('Executable does not match the dump image size/time stamp')

    def read(self, address, length):
        for start, size, offset in self.regions:
            if start <= address and address + length <= start + size:
                pos = offset + address - start
                if pos + length <= len(self.data):
                    return self.data[pos:pos + length]
        if self.image and self.base <= address and address + length <= self.base + self.size:
            return self.image.read(address - self.base, length)
        return None

    def pointer(self, address):
        data = self.read(address, 8)
        if data is None:
            return None
        value = struct.unpack('<Q', data)[0]
        # Pointers in disk PE metadata use the preferred image base, before ASLR.
        if self.image and self.image.preferred_base <= value < self.image.preferred_base + self.image.size:
            return self.base + value - self.image.preferred_base
        return value

    def rtti(self, address):
        vtable = self.pointer(address)
        if not vtable or not self.base <= vtable < self.base + self.size:
            return None
        result = {'vtable_rva': hex(vtable - self.base), 'type': None}
        locator = self.pointer(vtable - 8)
        metadata = self.read(locator, 24) if locator else None
        if metadata is None:
            return result
        signature, _, _, type_rva, _, self_rva = struct.unpack('<6I', metadata)
        if signature != 1 or locator - self_rva != self.base:
            return result
        data = self.read(self.base + type_rva + 16, 192)
        if data:
            name = data.split(b'\0')[0]
            if name.startswith((b'.?AV', b'.?AU')):
                result['type'] = name.decode('ascii', errors='replace')
        return result


def inspect(path, image_path=None):
    dump = Minidump(path, PEImage(image_path) if image_path else None)
    result = {'exception_code': hex(dump.code), 'fault_rva': hex(dump.address - dump.base),
              'access': {0: 'read', 1: 'write', 8: 'execute'}.get(dump.parameters[0]) if dump.parameters else None,
              'invalid_address': hex(dump.parameters[1]) if len(dump.parameters) > 1 else None,
              'rax_is_null': dump.registers['rax'] == 0,
              'matching_executable_supplied': image_path is not None,
              'objects': {name: dump.rtti(dump.registers[name]) for name in ['rcx', 'r12', 'r14']}}
    first = dump.pointer(dump.registers['r15'])
    if first:
        result['objects']['current_loop_item'] = dump.rtti(first)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('dump', type=Path)
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    rendered = json.dumps(inspect(args.dump, args.exe), indent=2)
    if args.report:
        args.report.write_text(rendered + '\n')
    print(rendered)
