#!/usr/bin/env python3
"""Pack a minimal legacy Android boot header v1 from explicit build inputs.

This does NOT sign an image, modify AVB/DTBO, or prove ABL compatibility. Header
addresses/page size come from the PCAM00 F.55 sample; loading is UNTESTED.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct

PAGE = 4096
CMDLINE = "rdinit=/init loglevel=7 ignore_loglevel printk.time=1 panic=15 clk_ignore_unused pd_ignore_unused"


def pack(image, dtb, ramdisk):
    if image[56:60] != b"ARM\x64":
        raise ValueError("Not an ARM64 Linux Image")
    if dtb[:4] != b"\xd0\x0d\xfe\xed" or struct.unpack_from(">I", dtb, 4)[0] != len(dtb):
        raise ValueError("Invalid DTB length/magic")
    if gzip.decompress(ramdisk)[:6] != b"070701":
        raise ValueError("Expected gzip newc initramfs")
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
    cmd = CMDLINE.encode("ascii")
    h[64:64+len(cmd)] = cmd
    ident = hashlib.sha1()
    for chunk in (kernel, ramdisk, b""):
        ident.update(chunk)
        ident.update(struct.pack("<I", len(chunk)))
    ident.update(struct.pack("<I", 0))  # recovery_dtbo_size in v1
    h[576:596] = ident.digest()
    struct.pack_into("<IQI", h, 1632, 0, 0, 1648)
    def padded(x):
        return x + b"\0" * ((-len(x)) % PAGE)
    out = bytes(h) + padded(kernel) + padded(ramdisk)
    if len(out) >= 64 * 1024 * 1024:
        raise ValueError("Exceeds sample's 64 MiB boot partition size")
    return out, kernel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--dtb", required=True, type=Path)
    ap.add_argument("--ramdisk", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    result, kernel = pack(args.image.read_bytes(), args.dtb.read_bytes(), args.ramdisk.read_bytes())
    with args.output.open("xb") as f:
        f.write(result)
    manifest = {
        "status": "COMPILE_ONLY_UNTESTED_DO_NOT_FLASH",
        "header_version": 1, "page_size": PAGE,
        "kernel_addr": "0x00008000", "ramdisk_addr": "0x01000000", "tags_addr": "0x00000100",
        "kernel_encoding": "gzip ARM64 Image + single Reno DTB",
        "cmdline": CMDLINE, "size": len(result), "kernel_field_size": len(kernel),
        "sha256": hashlib.sha256(result).hexdigest(),
        "verified_boot": "No AVB signature; bootloader handoff and stock DTBO application unverified",
    }
    with args.output.with_suffix(".json").open("x") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
