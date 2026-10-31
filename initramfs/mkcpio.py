#!/usr/bin/env python3
"""Emit a root-owned newc archive, including /dev/console without privileged mknod."""
import os
from pathlib import Path
import stat
import sys


def archive(root, epoch, out):
    if not 0 <= epoch <= 0xffffffff:
        raise ValueError('SOURCE_DATE_EPOCH exceeds newc range')
    ino = 0

    def entry(name, mode, data=b'', major=0, minor=0):
        nonlocal ino
        ino += 1
        name = os.fsencode(name) + b'\0'
        fields = (ino, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1,
                  epoch, len(data), 0, 0, major, minor, len(name), 0)
        out.write(b'070701' + ''.join(f'{n:08x}' for n in fields).encode())
        out.write(name)
        out.write(b'\0' * (-(110 + len(name)) % 4))
        out.write(data)
        out.write(b'\0' * (-len(data) % 4))

    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            entry(name, stat.S_IFLNK | 0o777, os.fsencode(os.readlink(path)))
        elif stat.S_ISDIR(mode):
            entry(name, stat.S_IFDIR | 0o755)
        elif stat.S_ISREG(mode):
            executable = name in {'init', 'bin/busybox'} or mode & 0o111
            entry(name, stat.S_IFREG | (0o755 if executable else 0o644), path.read_bytes())
        else:
            raise ValueError(f'Unexpected input node: {name}')
    if (root / 'dev/console').exists():
        raise ValueError('console must be created by archive writer only')
    entry('dev/console', stat.S_IFCHR | 0o600, major=5, minor=1)
    entry('TRAILER!!!', 0)


if __name__ == '__main__':
    archive(Path(sys.argv[1]), int(sys.argv[2]), sys.stdout.buffer)
