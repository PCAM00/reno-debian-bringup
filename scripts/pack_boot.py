#!/usr/bin/env python3
"""Pack the fixed, unsigned PCAM00 Android boot v1 diagnostic envelope.

No AVB/signature, second stage or DTBO is added. Structural validation does not
prove bootloader compatibility; the result remains UNTESTED, DO NOT FLASH.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

from image_formats import (MAX_COMPRESSED, MAX_DTB, MAX_IMAGE, parse_arm64_image,
                           parse_fdt, parse_ramdisk, read_regular)

PAGE = 4096
CMDLINE = "rdinit=/init loglevel=7 ignore_loglevel printk.time=1 panic=15 clk_ignore_unused pd_ignore_unused"
BOOT_LIMIT = 64 * 1024 * 1024


def command_bytes(cmdline):
    if not isinstance(cmdline, str):
        raise ValueError("cmdline must be ASCII text")
    try:
        encoded = cmdline.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("cmdline must contain only printable ASCII") from exc
    if len(encoded) > 511 or any(c < 32 or c > 126 for c in encoded):
        raise ValueError("cmdline exceeds 511 bytes or contains NUL/nonprintable bytes")
    return encoded


def pack(image, dtb, ramdisk, cmdline=None):
    cmd = command_bytes(CMDLINE if cmdline is None else cmdline)
    parse_arm64_image(image)
    parse_fdt(dtb)
    parse_ramdisk(ramdisk)
    compressed = bytearray(gzip.compress(image, compresslevel=9, mtime=0))
    # Python 3.11/3.12 expose zlib's OS=3, newer Python forces OS=255.
    # Normalize this metadata byte so cross-host headers/IDs match.
    compressed[9] = 255
    kernel = bytes(compressed) + dtb
    h = bytearray(PAGE)
    h[:8] = b"ANDROID!"
    # kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size,
    # second_addr, tags_addr, page_size, header_version, os_version
    struct.pack_into("<10I", h, 8, len(kernel), 0x8000, len(ramdisk), 0x1000000,
                     0, 0, 0x100, PAGE, 1, 0)
    h[48:64] = b"pcam00-ramdiag".ljust(16, b"\0")
    h[64:64 + len(cmd)] = cmd
    ident = hashlib.sha1()
    for chunk in (kernel, ramdisk, b""):
        ident.update(chunk)
        ident.update(struct.pack("<I", len(chunk)))
    ident.update(struct.pack("<I", 0))  # recovery_dtbo_size in v1
    h[576:596] = ident.digest()
    struct.pack_into("<IQI", h, 1632, 0, 0, 1648)

    def padded(data):
        return data + b"\0" * ((-len(data)) % PAGE)

    size = PAGE + ((len(kernel) + PAGE - 1) // PAGE) * PAGE
    size += ((len(ramdisk) + PAGE - 1) // PAGE) * PAGE
    if size >= BOOT_LIMIT:
        raise ValueError("Exceeds sample's 64 MiB boot partition size")
    return bytes(h) + padded(kernel) + padded(ramdisk), kernel


def boot_manifest(result, kernel, cmdline=CMDLINE):
    return {
        "status": "COMPILE_ONLY_UNTESTED_DO_NOT_FLASH",
        "stage": "compile-only; not flashed or booted on Reno",
        "hardware_tested": False,
        "header_version": 1, "page_size": PAGE,
        "kernel_addr": "0x00008000", "ramdisk_addr": "0x01000000", "tags_addr": "0x00000100",
        "kernel_encoding": "gzip ARM64 Image + single Reno DTB",
        "cmdline": cmdline, "size": len(result), "kernel_field_size": len(kernel),
        "sha256": hashlib.sha256(result).hexdigest(),
        "verified_boot": "No AVB signature; bootloader handoff and stock DTBO application unverified",
    }


def output_paths(output):
    # Resolve the parent only: an existing (including dangling) output symlink
    # must be refused, not followed to its target.
    output = Path(output)
    output = output.parent.resolve(strict=True) / output.name
    manifest = output.with_suffix(".json")
    if output == manifest:
        raise ValueError("Image and manifest output paths must differ")
    for path in (output, manifest):
        if os.path.lexists(path):
            raise FileExistsError(f"Refusing existing image/manifest output: {path}")
    return output, manifest


def write_pair(output, result, manifest):
    """Reserve both names exclusively before writing either payload.

    A race cannot overwrite an existing path. On failure only our still-empty
    reservation is removed; disk/write failures may leave an incomplete pair
    that a subsequent run will correctly refuse, never silently reuse.
    """
    output, manifest_path = output_paths(output)
    text = json.dumps(manifest, indent=2) + "\n"
    with output.open("xb") as image_stream:
        image_stat = os.fstat(image_stream.fileno())
        try:
            manifest_stream = manifest_path.open("x", encoding="utf-8")
        except OSError:
            now = output.lstat()
            if (now.st_dev, now.st_ino, now.st_size) == (image_stat.st_dev, image_stat.st_ino, 0):
                # Both paths were resolved/preflighted above; do not remove a
                # path replaced by another writer since reservation.
                output.unlink()
            raise
        with manifest_stream:
            image_stream.write(result)
            image_stream.flush()
            manifest_stream.write(text)
    return manifest_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--dtb", required=True, type=Path)
    ap.add_argument("--ramdisk", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--cmdline", default=CMDLINE,
                    help="printable ASCII, at most 511 bytes; diagnostic default is recommended")
    args = ap.parse_args(argv)
    try:
        output_paths(args.output)  # refuse either pre-existing result before reading inputs
        result, kernel = pack(read_regular(args.image, MAX_IMAGE),
                              read_regular(args.dtb, MAX_DTB),
                              read_regular(args.ramdisk, MAX_COMPRESSED), args.cmdline)
        manifest = boot_manifest(result, kernel, args.cmdline)
        write_pair(args.output, result, manifest)
    except (ValueError, OSError) as exc:
        print(f"pack_boot: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
