#!/usr/bin/env python3
"""Fail-closed audit of the resolved U-Boot configuration and the built binary.

This is a static check. Passing it means the build contains no storage driver,
no flashing command and no persistent environment - it does NOT mean the image
boots on a phone.
"""
import argparse
import json
import re
import struct
import sys

REQUIRED_ON = {
    'EFI_LOADER': 'UEFI application support must be built in',
    'EFI_BINARY_EXEC': 'loading an EFI binary is the point of the prototype',
    'CMD_BOOTEFI': 'need the bootefi command',
    'CMD_BOOTEFI_BINARY': 'need bootefi <addr> to run a raw EFI application',
    'EFI_MEDIA': 'selects BLK without any physical block device behind it',
    'EFI_VARIABLE_NO_STORE': 'UEFI variables must stay in RAM',
    'ENV_IS_NOWHERE': 'the U-Boot environment must never be stored',
    'BLK': 'EFI_LOADER depends on BLK; EFI_MEDIA provides the empty uclass',
    'SYS_DEVICE_NULLDEV': 'console-less baseline needs the null device',
}

FORBIDDEN_ON = {
    'FASTBOOT': 'fastboot flashing surface',
    'USB_FUNCTION_FASTBOOT': 'fastboot gadget',
    'CMD_DFU': 'DFU update command',
    'DFU_MMC': 'DFU over MMC',
    'DFU_SCSI': 'DFU over SCSI (this is how UFS is reached)',
    'CMD_BCB': 'bootloader control block writes to misc',
    'CMD_SAVEENV': 'would persist the environment',
    'CMD_GPT': 'partition table editing',
    'CMD_MEMORY': 'raw memory read/write from the console',
    'CMD_IO': 'raw MMIO poking from the console',
    'CMD_GPIO': 'GPIO poking from the console',
    'CMD_I2C': 'I2C poking from the console',
    'CMD_EEPROM': 'EEPROM access',
    'CMD_REGULATOR': 'regulator poking from the console',
    'CMD_USB_MASS_STORAGE': 'exposes the phone as a disk to a host',
    'CMD_NVEDIT_EFI': 'edits EFI variables',
    'SCSI': 'generic SCSI block layer (UFS path); registers .write/.erase',
    'UFS': 'UFS storage',
    'UFS_QCOM': 'Qualcomm UFS host',
    'MMC': 'SD/eMMC storage',
    'MMC_WRITE': 'MMC write support',
    'NVME': 'NVMe storage',
    'MTD': 'raw flash storage',
    'USB_STORAGE': 'USB mass storage host',
    'FAT_WRITE': 'writable FAT (implied by EFI_LOADER, must be overridden)',
    'EXT4_WRITE': 'writable ext4',
    'EFI_VARIABLE_FILE_STORE': 'persistent EFI variables on a file system',
    'EFI_VARIABLE_SF_STORE': 'persistent EFI variables in SPI flash',
    'EFI_MEDIA_BLK': 'exposes EFI block devices for real media',
    'EFI_BOOTMGR': 'UEFI boot manager scanning for boot entries',
    'CMD_BOOTEFI_BOOTMGR': 'boot manager command',
    'EFI_CAPSULE_ON_DISK': 'firmware capsule update from disk',
    'EFI_CAPSULE_ON_DISK_EARLY': 'early capsule processing',
    'EFI_RUNTIME_UPDATE_CAPSULE': 'runtime capsule update',
    'EFI_CAPSULE_FIRMWARE_MANAGEMENT': 'FMP firmware update',
    'EFI_CAPSULE_FIRMWARE_RAW': 'raw firmware capsule',
    'BOOTSTD': 'distro boot (scans storage)',
    'BOOTSTD_FULL': 'distro boot',
    'USE_PREBOOT': 'runs a preboot command before the console',
    'BOOTMETH_ANDROID': 'Android boot method touching BCB/misc',
}

# Substrings that must not occur in the built binary.
# NOTE: the ELF symbol/string tables legitimately contain names such as
# FASTBOOT_UDP (a networking environment variable) even when the fastboot
# subsystem is not built.  That is why the ELF is scanned over its PT_LOAD
# segments only - those are exactly the bytes that get loaded into memory.
BINARY_FATAL = (b'saveenv', b'fastboot')
BINARY_WARN = (b'bootloader control block', b'dfu', b'eraseblk', b'gpt')

EXPECTED_DEVICE_TREE = 'qcom/sdm710-oppo-pcam00-uefi-memonly'


def parse_config(path):
    cfg = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                m = re.match(r'#\s*(CONFIG_[A-Za-z0-9_]+)\s+is not set$', line)
                if m:
                    cfg[m.group(1)[7:]] = 'n'
                continue
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.startswith('CONFIG_'):
                cfg[key[7:]] = value.strip().strip('"')
    return cfg


def audit(cfg):
    errors, notes = [], []

    for name, why in REQUIRED_ON.items():
        value = cfg.get(name, 'n')
        if value in ('n', '') or value == '0':
            errors.append(f'{name} must be enabled: {why}')
    for name, why in FORBIDDEN_ON.items():
        value = cfg.get(name, 'n')
        if value not in ('n', '', '0'):
            errors.append(f'{name}={value} must be off: {why}')

    dt = cfg.get('DEFAULT_DEVICE_TREE', '')
    if dt != EXPECTED_DEVICE_TREE:
        errors.append(f'DEFAULT_DEVICE_TREE is "{dt}", expected "{EXPECTED_DEVICE_TREE}"')

    envfile = cfg.get('ENV_DEFAULT_ENV_TEXT_FILE', '')
    if 'oppo/pcam00' not in envfile:
        errors.append(f'ENV_DEFAULT_ENV_TEXT_FILE is "{envfile}", expected the Reno board env')

    delay = cfg.get('BOOTDELAY', '')
    if delay not in ('-1',):
        errors.append(f'BOOTDELAY={delay} must be -1 so nothing auto-boots')

    for name in ('ENV_IS_IN_MMC', 'ENV_IS_IN_SPI_FLASH', 'ENV_IS_IN_NAND',
                 'ENV_IS_IN_FAT', 'ENV_IS_IN_EXT4', 'ENV_IS_IN_REMOTE'):
        if cfg.get(name, 'n') not in ('n', '', '0'):
            errors.append(f'{name} must be off: environment must not persist anywhere')

    if cfg.get('COMPILE_TEST', 'n') not in ('n', '', '0'):
        errors.append('COMPILE_TEST must stay off: it disables OF_UPSTREAM and the custom env')

    notes.append(f'{len(REQUIRED_ON)} required options enabled')
    notes.append(f'{len(FORBIDDEN_ON)} write-capable options verified off')
    return errors, notes


def elf_load_ranges(data):
    """Return the (start, end) file ranges of PT_LOAD segments of an ELF64 file.

    Returns None when the data is not a little/big-endian ELF64 image.
    """
    if len(data) < 64 or data[:4] != b'\x7fELF' or data[4] != 2:
        return None
    endian = '<' if data[5] == 1 else '>'
    e_phoff, = struct.unpack_from(endian + 'Q', data, 0x20)
    e_phentsize, e_phnum = struct.unpack_from(endian + 'HH', data, 0x36)
    if not e_phoff or not e_phnum or e_phentsize < 56:
        return None
    ranges = []
    for index in range(e_phnum):
        off = e_phoff + index * e_phentsize
        if off + 56 > len(data):
            break
        p_type, = struct.unpack_from(endian + 'I', data, off)
        p_offset, = struct.unpack_from(endian + 'Q', data, off + 8)
        p_filesz, = struct.unpack_from(endian + 'Q', data, off + 32)
        if p_type == 1 and p_filesz and p_offset + p_filesz <= len(data):
            ranges.append((p_offset, p_offset + p_filesz))
    return ranges or None


def scan_binary(path, label, errors, notes, warnings, load_only=False):
    with open(path, 'rb') as fh:
        data = fh.read()
    if len(data) < 4096:
        errors.append(f'{label} is only {len(data)} bytes')
        return
    if load_only:
        ranges = elf_load_ranges(data)
        if not ranges:
            errors.append(f'{label}: cannot determine ELF load segments; refusing to guess')
            return
        payload = b''.join(data[start:end] for start, end in ranges)
        notes.append(f'{label}: {len(ranges)} PT_LOAD segment(s), '
                     f'{len(payload)} of {len(data)} bytes are loaded')
    else:
        payload = data
    low = payload.lower()
    for needle in BINARY_FATAL:
        if needle in low:
            errors.append(f'{label} contains forbidden string {needle!r}')
    for needle in BINARY_WARN:
        if needle in low:
            warnings.append(f'{label} loaded image contains {needle!r}; review it')
    notes.append(f'{label}: {len(payload)} bytes scanned')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--elf')
    ap.add_argument('--bin')
    ap.add_argument('--output')
    args = ap.parse_args()

    cfg = parse_config(args.config)
    errors, notes = audit(cfg)
    warnings = []
    if args.elf:
        scan_binary(args.elf, 'u-boot.elf', errors, notes, warnings, load_only=True)
    if args.bin:
        scan_binary(args.bin, 'u-boot.bin', errors, notes, warnings)

    report = {
        'result': 'PASS' if not errors else 'FAIL',
        'scope': 'static configuration and binary-string audit only; '
                 'this is NOT evidence that the image boots on a Reno',
        'device_tree': cfg.get('DEFAULT_DEVICE_TREE'),
        'bootdelay': cfg.get('BOOTDELAY'),
        'checks': notes,
        'warnings': warnings,
        'errors': errors,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as fh:
            fh.write(text + '\n')
    print(text)
    return 0 if not errors else 1


if __name__ == '__main__':
    sys.exit(main())
