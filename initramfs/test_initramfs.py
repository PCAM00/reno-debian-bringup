#!/usr/bin/env python3
"""Offline tests. Never run the target /init on the build host."""
import gzip
import importlib.util
import io
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('mkcpio', HERE / 'mkcpio.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def parse(data):
    result = {}
    pos = 0
    while True:
        assert data[pos:pos + 6] == b'070701'
        fields = [int(data[pos + 6 + n * 8:pos + 14 + n * 8], 16) for n in range(13)]
        pos += 110
        name = data[pos:pos + fields[11] - 1].decode()
        pos = (pos + fields[11] + 3) & ~3
        payload = data[pos:pos + fields[6]]
        pos = (pos + fields[6] + 3) & ~3
        result[name] = (fields, payload)
        if name == 'TRAILER!!!':
            return result


class Tests(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(['bash', '-n', str(HERE / 'build.sh')], check=True)
        subprocess.run(['sh', '-n', str(HERE / 'init')], check=True)

    def test_newc(self):
        with tempfile.TemporaryDirectory(prefix='.test-', dir=HERE) as tmp:
            root = Path(tmp)
            (root / 'dev').mkdir()
            (root / 'bin').mkdir()
            (root / 'bin/busybox').write_bytes(b'fixture')
            has_symlinks = True
            try:
                (root / 'bin/sh').symlink_to('busybox')
            except PermissionError:
                has_symlinks = False  # Android shared storage cannot create symlinks.
            (root / 'init').write_bytes(b'#!/bin/sh\n')
            (root / 'init').chmod(0o755)
            stream = io.BytesIO()
            mod.archive(root, 42, stream)
            entries = parse(gzip.decompress(gzip.compress(stream.getvalue(), mtime=0)))
            f, _ = entries['dev/console']
            self.assertEqual(f[1], stat.S_IFCHR | 0o600)
            self.assertEqual(f[2:4], [0, 0])
            self.assertEqual(f[9:11], [5, 1])
            if has_symlinks:
                self.assertEqual(entries['bin/sh'][1], b'busybox')
            self.assertEqual(entries['init'][0][1], stat.S_IFREG | 0o755)
            self.assertTrue(all(v[0][5] == 42 for v in entries.values()))
            stream2 = io.BytesIO()
            mod.archive(root, 42, stream2)
            self.assertEqual(stream.getvalue(), stream2.getvalue())

    def test_ram_only_surface(self):
        text = (HERE / 'init').read_text()
        for line in text.splitlines():
            if line.startswith('mount -t '):
                self.assertIn(line.split()[2], {'proc', 'sysfs', 'devtmpfs', 'tmpfs', 'configfs'})
        for command in ('mke2fs', 'mkfs.', 'fsck', 'resize2fs', '/dev/block', 'getty ', 'telnetd ', 'sshd ', 'nc -'):
            self.assertNotIn(command, text)
        self.assertIn('-u 65534:65534', text)
        self.assertIn('remount,ro,nosuid,nodev,noexec tmpfs /www', text)


if __name__ == '__main__':
    unittest.main()
