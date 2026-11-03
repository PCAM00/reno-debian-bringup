#!/usr/bin/env python3
"""Prove the auditor is fail-closed: each mutation must be rejected."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('vc', HERE / 'validate_config.py')
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)


def baseline():
    cfg = {name: 'y' for name in vc.REQUIRED_ON}
    cfg.update({
        'DEFAULT_DEVICE_TREE': vc.EXPECTED_DEVICE_TREE,
        'ENV_DEFAULT_ENV_TEXT_FILE': 'board/oppo/pcam00/pcam00-uefi-memonly.env',
        'BOOTDELAY': '-1',
    })
    for name in vc.FORBIDDEN_ON:
        cfg[name] = 'n'
    return cfg


class Tests(unittest.TestCase):
    def test_baseline_passes(self):
        errors, _ = vc.audit(baseline())
        self.assertEqual(errors, [])

    def test_each_forbidden_option_is_caught(self):
        for name in vc.FORBIDDEN_ON:
            cfg = baseline()
            cfg[name] = 'y'
            errors, _ = vc.audit(cfg)
            self.assertTrue(errors, f'{name}=y was not rejected')
            self.assertTrue(any(name in e for e in errors), f'{name} not named: {errors}')

    def test_each_required_option_is_caught(self):
        for name in vc.REQUIRED_ON:
            cfg = baseline()
            cfg[name] = 'n'
            errors, _ = vc.audit(cfg)
            self.assertTrue(any(name in e for e in errors), f'{name}=n not caught')

    def test_wrong_device_tree_and_bootdelay(self):
        cfg = baseline()
        cfg['DEFAULT_DEVICE_TREE'] = 'qcom/sdm845-db845c'
        self.assertTrue(vc.audit(cfg)[0])
        cfg = baseline()
        cfg['BOOTDELAY'] = '1'
        self.assertTrue(vc.audit(cfg)[0])
        cfg = baseline()
        cfg['COMPILE_TEST'] = 'y'
        self.assertTrue(vc.audit(cfg)[0])

    def test_parse_handles_is_not_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.config'
            path.write_text('CONFIG_EFI_LOADER=y\n# CONFIG_SCSI is not set\n')
            cfg = vc.parse_config(path)
        self.assertEqual(cfg['EFI_LOADER'], 'y')
        self.assertEqual(cfg['SCSI'], 'n')

    def test_binary_scan_rejects_write_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / 'good.bin'
            good.write_bytes(b'\x7fELF' + b'example payload' * 1000)
            errors, notes = vc.audit(baseline())
            warnings = []
            vc.scan_binary(good, 'good.bin', errors, notes, warnings)
            self.assertEqual(errors, [])
            for needle in vc.BINARY_FATAL:
                bad = Path(tmp) / 'bad.bin'
                bad.write_bytes(b'\x7fELF' + needle + b' padding' * 1000)
                errors = []
                vc.scan_binary(bad, 'bad.bin', errors, [], [])
                self.assertTrue(errors, f'{needle!r} not detected')

    def test_tiny_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / 'small.bin'
            small.write_bytes(b'\x7fELF')
            errors = []
            vc.scan_binary(small, 'small.bin', errors, [], [])
            self.assertTrue(errors)

    def test_elf_scan_ignores_symbol_strings(self):
        """A name in .strtab must not be mistaken for loaded code."""
        import struct

        def fake_elf(needle, offset):
            data = bytearray(8192)
            data[0:4] = b'\x7fELF'
            data[4] = 2          # ELF64
            data[5] = 1          # little endian
            struct.pack_into('<Q', data, 0x20, 64)   # e_phoff
            struct.pack_into('<HH', data, 0x36, 56, 1)  # e_phentsize, e_phnum
            struct.pack_into('<I', data, 64, 1)      # PT_LOAD
            struct.pack_into('<Q', data, 64 + 8, 128)    # p_offset
            struct.pack_into('<Q', data, 64 + 32, 512)   # p_filesz
            data[offset:offset + len(needle)] = needle
            return bytes(data)

        inside = fake_elf(b'fastboot', 200)
        ranges = vc.elf_load_ranges(inside)
        self.assertEqual(ranges, [(128, 640)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inside.elf'
            path.write_bytes(inside)
            errors = []
            vc.scan_binary(path, 'inside.elf', errors, [], [], load_only=True)
            self.assertTrue(errors, 'string inside PT_LOAD must be caught')

            outside = fake_elf(b'fastboot', 5000)   # beyond 128+512
            path2 = Path(tmp) / 'outside.elf'
            path2.write_bytes(outside)
            errors = []
            vc.scan_binary(path2, 'outside.elf', errors, [], [], load_only=True)
            self.assertEqual(errors, [], 'symbol-table string must be ignored')

    def test_unreadable_elf_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'notelf.bin'
            path.write_bytes(b'MZ' + b'x' * 8192)
            errors = []
            vc.scan_binary(path, 'notelf.bin', errors, [], [], load_only=True)
            self.assertTrue(errors, 'an unparsable ELF must fail closed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
