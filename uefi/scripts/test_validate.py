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


if __name__ == '__main__':
    unittest.main(verbosity=2)
