#!/usr/bin/env python3
"""Synthetic binary fixtures/negative controls; never run target code or tools."""
import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock

import pack_boot
from image_formats import (gzip_member, parse_arm64_image, parse_fdt,
                           parse_newc, parse_ramdisk, read_regular)
from pack_boot import PAGE, pack


def arm64_fixture(size=256):
    image = bytearray(size)
    struct.pack_into("<IIQQQ", image, 0, 0x14000010, 0, 0x80000, size, 0)
    image[56:60] = b"ARM\x64"
    return bytes(image)


def dtb_fixture(nodes=None):
    """Serialize a real v17 FDT without requiring dtc, including parent nodes."""
    if nodes is None:
        nodes = {"/": {"compatible": b"test,synthetic\0"}}
    tree = {"/": {}}
    for path, props in nodes.items():
        parts = path.strip("/").split("/") if path != "/" else []
        for count in range(1, len(parts) + 1):
            tree.setdefault("/" + "/".join(parts[:count]), {})
        tree[path].update(props)
    names = sorted({name for props in tree.values() for name in props})
    strings, offsets = bytearray(), {}
    for name in names:
        offsets[name] = len(strings)
        strings.extend(name.encode("ascii") + b"\0")
    body = bytearray()

    def word(value):
        body.extend(struct.pack(">I", value))

    def padded(value):
        body.extend(value)
        body.extend(bytes(-len(body) % 4))

    def node(path):
        word(1)
        padded((path.rsplit("/", 1)[-1] if path != "/" else "").encode() + b"\0")
        for name, value in tree[path].items():
            word(3)
            word(len(value))
            word(offsets[name])
            padded(value)
        for child in sorted(tree):
            if child != "/" and (child.rsplit("/", 1)[0] or "/") == path:
                node(child)
        word(2)

    node("/")
    word(9)
    off_struct = 56
    off_strings = off_struct + len(body)
    header = struct.pack(">10I", 0xd00dfeed, off_strings + len(strings), off_struct,
                         off_strings, 40, 17, 16, 0, len(strings), len(body))
    return header + bytes(16) + body + strings


def cpio_entry(name, mode=stat.S_IFREG | 0o644, data=b"", *, uid=0, gid=0,
               nlink=None, major=0, minor=0, ino=1):
    raw_name = name.encode("utf-8") + b"\0"
    if nlink is None:
        nlink = 2 if stat.S_ISDIR(mode) else 1
    fields = (ino, mode, uid, gid, nlink, 0, len(data), 0, 0, major, minor, len(raw_name), 0)
    header = b"070701" + b"".join(f"{n:08x}".encode() for n in fields)
    return header + raw_name + bytes(-(110 + len(raw_name)) % 4) + data + bytes(-len(data) % 4)


def cpio_fixture(records=None):
    if records is None:
        records = [cpio_entry("init", stat.S_IFREG | 0o755, b"#!/bin/sh\n# synthetic only\n")]
    return b"".join(records) + cpio_entry("TRAILER!!!", 0)


def ramdisk_fixture(records=None):
    return gzip.compress(cpio_fixture(records), mtime=0)


def replace_u32(data, offset, value):
    changed = bytearray(data)
    struct.pack_into(">I", changed, offset, value)
    return bytes(changed)


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.image = arm64_fixture()
        self.dtb = dtb_fixture()
        self.rd = ramdisk_fixture()

    def test_roundtrip_and_offsets(self):
        boot, kernel = pack(self.image, self.dtb, self.rd)
        self.assertEqual(boot[:8], b"ANDROID!")
        fields = struct.unpack_from("<10I", boot, 8)
        self.assertEqual(fields, (len(kernel), 0x8000, len(self.rd), 0x1000000, 0, 0, 0x100, PAGE, 1, 0))
        self.assertEqual(len(boot) % PAGE, 0)
        self.assertEqual(boot[PAGE:PAGE + len(kernel)], kernel)
        self.assertEqual(kernel[9], 255)
        recovered, consumed = gzip_member(kernel, 1024, allow_trailing=True)
        self.assertEqual(recovered, self.image)
        self.assertEqual(kernel[consumed:], self.dtb)
        rd_start = PAGE + ((len(kernel) + PAGE - 1) // PAGE) * PAGE
        self.assertEqual(boot[rd_start:rd_start + len(self.rd)], self.rd)
        self.assertEqual(struct.unpack_from("<IQI", boot, 1632), (0, 0, 1648))
        self.assertEqual(len(boot[:PAGE]), 4096)
        self.assertEqual(pack(self.image, self.dtb, self.rd), (boot, kernel))

    def test_reject_invalid_image(self):
        for image in (b"bad", arm64_fixture()[:60], bytes(256)):
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                pack(image, self.dtb, self.rd)
        with self.assertRaises(ValueError):
            parse_arm64_image(arm64_fixture()[:56] + b"ARM\x64" + b"\0" * 3)

    def test_cmdline_boundary(self):
        boot, _ = pack(self.image, self.dtb, self.rd, "x" * 511)
        self.assertEqual(boot[64:576], b"x" * 511 + b"\0")
        for command in ("x" * 512, "a\0b", "x\ny", "\t", "\x1f", "\x7f", "é", b"bytes"):
            with self.subTest(command=repr(command[:20])), self.assertRaises(ValueError):
                pack(self.image, self.dtb, self.rd, command)

    def test_cmdline_global_is_validated(self):
        with mock.patch.object(pack_boot, "CMDLINE", "x" * 512), self.assertRaises(ValueError):
            pack(self.image, self.dtb, self.rd)

    def test_pack_rejects_bad_fdt_and_newc(self):
        for dtb, ramdisk in ((struct.pack(">II", 0xd00dfeed, 8), self.rd),
                             (self.dtb + self.dtb, self.rd),
                             (self.dtb, gzip.compress(b"070701test", mtime=0)),
                             (self.dtb, ramdisk_fixture([cpio_entry("../init")]))):
            with self.subTest(dtb_size=len(dtb)), self.assertRaises(ValueError):
                pack(self.image, dtb, ramdisk)
        with mock.patch.object(pack_boot, "BOOT_LIMIT", 3 * PAGE), self.assertRaises(ValueError):
            pack(self.image, self.dtb, self.rd)

    def test_pair_no_clobber(self):
        boot, kernel = pack(self.image, self.dtb, self.rd)
        manifest = pack_boot.boot_manifest(boot, kernel)
        for existing in ("boot.img", "boot.json"):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / existing).write_bytes(b"keep")
                with self.assertRaises(FileExistsError):
                    pack_boot.write_pair(root / "boot.img", boot, manifest)
                self.assertEqual((root / existing).read_bytes(), b"keep")
                other = "boot.json" if existing == "boot.img" else "boot.img"
                self.assertFalse((root / other).exists())

    def test_pair_rejects_dangling_symlink_and_identical_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "boot.img"
            output.with_suffix(".json").symlink_to("missing-target")
            with self.assertRaises(FileExistsError):
                pack_boot.output_paths(output)
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                pack_boot.output_paths(Path(temporary) / "same.json")

    def test_pair_reserves_both_before_data_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "boot.img"
            original = Path.open

            def collide(path, *args, **kwargs):
                if path.suffix == ".json":
                    with original(path, "wb") as stream:
                        stream.write(b"racing existing manifest")
                    raise FileExistsError("synthetic competing writer")
                return original(path, *args, **kwargs)

            with mock.patch.object(Path, "open", collide), self.assertRaises(FileExistsError):
                pack_boot.write_pair(output, b"image", {})
            self.assertFalse(output.exists())
            self.assertEqual(output.with_suffix(".json").read_bytes(), b"racing existing manifest")

    def test_cli_writes_pair_only_on_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, payload in (("Image", self.image), ("board.dtb", self.dtb), ("rd.gz", self.rd)):
                (root / name).write_bytes(payload)
            args = ["--image", str(root / "Image"), "--dtb", str(root / "board.dtb"),
                    "--ramdisk", str(root / "rd.gz"), "--output", str(root / "boot.img")]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(pack_boot.main(args + ["--cmdline", "x" * 512]), 1)
            self.assertFalse((root / "boot.img").exists())
            self.assertFalse((root / "boot.json").exists())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pack_boot.main(args), 0)
            manifest = json.loads((root / "boot.json").read_text())
            self.assertFalse(manifest["hardware_tested"])
            self.assertEqual(manifest["size"], (root / "boot.img").stat().st_size)
            before = (root / "boot.img").read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(pack_boot.main(args), 1)
            self.assertEqual((root / "boot.img").read_bytes(), before)

    def test_input_reader_refuses_symlinks_and_nonregular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plain").write_bytes(b"1234")
            self.assertEqual(read_regular(root / "plain", 4), b"1234")
            with self.assertRaises(ValueError):
                read_regular(root / "plain", 3)
            (root / "link").symlink_to("plain")
            with self.assertRaises((ValueError, OSError)):
                read_regular(root / "link", 4)
            os.mkfifo(root / "fifo")
            with self.assertRaises(ValueError):
                read_regular(root / "fifo", 4)


class FdtTests(unittest.TestCase):
    def test_valid_structure_roundtrip(self):
        nodes = {"/": {"compatible": b"test,synthetic\0"}, "/soc": {"#address-cells": bytes(4)},
                 "/soc/device@1000": {"reg": bytes(16)}}
        self.assertEqual(parse_fdt(dtb_fixture(nodes)), nodes)
        self.assertEqual(parse_fdt(dtb_fixture({"/": {}})), {"/": {}})

    def test_all_truncations_rejected(self):
        dtb = dtb_fixture()
        for length in range(len(dtb)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                parse_fdt(dtb[:length])

    def test_header_bounds_overlap_and_versions(self):
        dtb = dtb_fixture()
        invalid = [(0, 0), (4, len(dtb) + 1), (8, len(dtb) + 8), (8, 57),
                   (12, len(dtb) + 4), (16, 41), (16, 56), (20, 16), (20, 18),
                   (24, 18), (32, len(dtb)), (36, 0xffffffff), (12, 56)]
        for offset, value in invalid:
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                parse_fdt(replace_u32(dtb, offset, value))

    def test_structure_terminators_duplicates_and_padding(self):
        empty = dtb_fixture({"/": {}})
        # Empty-root FDT structure: BEGIN, name+pad, END_NODE, END.
        invalid = [replace_u32(empty, 56, 2), replace_u32(empty, 64, 9),
                   replace_u32(empty, 68, 4), replace_u32(empty, 68, 0xffffffff),
                   replace_u32(empty, 60, 0x00000001)]
        # Duplicate sibling nodes and properties, with all declared sizes valid.
        def raw_body(body, strings=b"x\0"):
            start = 56 + len(body)
            return struct.pack(">10I", 0xd00dfeed, start + len(strings), 56, start,
                               40, 17, 16, 0, len(strings), len(body)) + bytes(16) + body + strings
        begin = struct.pack(">II", 1, 0)
        end = struct.pack(">II", 2, 9)
        prop = struct.pack(">IIII", 3, 1, 0, 0x01000000)
        child = struct.pack(">I", 1) + b"kid\0" + struct.pack(">I", 2)
        invalid += [raw_body(begin + prop + prop + end),
                    raw_body(begin + child + child + end),
                    raw_body(begin + child + prop + end),
                    raw_body(begin + end + struct.pack(">I", 4)),
                    raw_body(begin + struct.pack(">III", 3, 0xffffffff, 0) + end),
                    raw_body(begin + struct.pack(">III", 3, 0, 100) + end),
                    raw_body(begin + struct.pack(">IIII", 3, 1, 0, 0x01000001) + end)]
        for index, blob in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(ValueError):
                parse_fdt(blob)

    def test_trailers_and_reserve_termination(self):
        dtb = dtb_fixture()
        with self.assertRaises(ValueError):
            parse_fdt(dtb + dtb)
        trailer = replace_u32(dtb + b"evil", 4, len(dtb) + 4)
        with self.assertRaises(ValueError):
            parse_fdt(trailer)
        bad = bytearray(dtb)
        struct.pack_into(">QQ", bad, 40, 0x1000, 1)
        with self.assertRaises(ValueError):
            parse_fdt(bytes(bad))


class NewcTests(unittest.TestCase):
    def test_valid_and_zero_record_padding(self):
        plain = cpio_fixture()
        self.assertEqual(parse_newc(plain)["init"].mode, stat.S_IFREG | 0o755)
        self.assertEqual(parse_newc(plain + bytes(-len(plain) % 512)), parse_newc(plain))
        self.assertEqual(parse_ramdisk(gzip.compress(plain, mtime=0))[0], plain)

    def test_all_truncations_rejected(self):
        plain = cpio_fixture()
        for length in range(len(plain)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                parse_newc(plain[:length])

    def test_unsafe_paths_duplicates_owner_block_hardlink(self):
        for name in ("/init", "../init", "etc/../init", "./init", "etc//x", "etc/", "a\\b", "a\0b", "a\nb"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_newc(cpio_fixture([cpio_entry(name)]))
        invalid = [[cpio_entry("init"), cpio_entry("init")],
                   [cpio_entry("init", uid=1)], [cpio_entry("init", gid=1)],
                   [cpio_entry("disk", stat.S_IFBLK | 0o600, major=8)],
                   [cpio_entry("fifo", stat.S_IFIFO | 0o600)],
                   [cpio_entry("init", nlink=2)],
                   [cpio_entry("init", stat.S_IFREG | 0o4755)],
                   [cpio_entry("etc/x")]]
        for records in invalid:
            with self.subTest(records=records[0][:30]), self.assertRaises(ValueError):
                parse_newc(cpio_fixture(records))

    def test_symlinks_safe_relative_and_escape_cycles(self):
        base = [cpio_entry("bin", stat.S_IFDIR | 0o755), cpio_entry("bin/busybox", data=b"fixture")]
        entries = parse_newc(cpio_fixture(base + [cpio_entry("bin/sh", stat.S_IFLNK | 0o777, b"busybox")]))
        self.assertEqual(entries["bin/sh"].data, b"busybox")
        for target in (b"/bin/busybox", b"../../outside", b"missing", b"sh", b"busybox/child", b"bad\0"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                parse_newc(cpio_fixture(base + [cpio_entry("bin/sh", stat.S_IFLNK | 0o777, target)]))
        alias = [cpio_entry("alias", stat.S_IFLNK | 0o777, b"bin"), cpio_entry("alias/file")]
        with self.assertRaises(ValueError):
            parse_newc(cpio_fixture(base + alias))
        relative = base + [cpio_entry("etc", stat.S_IFDIR | 0o755),
                           cpio_entry("etc/shell", stat.S_IFLNK | 0o777, b"../bin/sh"),
                           cpio_entry("bin/sh", stat.S_IFLNK | 0o777, b"busybox")]
        self.assertIn("etc/shell", parse_newc(cpio_fixture(relative)))
        cycle = base + [cpio_entry("bin/sh", stat.S_IFLNK | 0o777, b"other"),
                        cpio_entry("bin/other", stat.S_IFLNK | 0o777, b"sh")]
        with self.assertRaises(ValueError):
            parse_newc(cpio_fixture(cycle))
        with self.assertRaises(ValueError):
            parse_newc(cpio_fixture([cpio_entry("etc/early"), cpio_entry("etc", stat.S_IFDIR | 0o755)]))

    def test_padding_trailer_and_hex_corruption(self):
        plain = cpio_fixture([cpio_entry("a", data=b"x")])
        invalid = [plain + b"evil", plain + plain, plain + bytes(512)]
        for offset in (6, 112 + 1, 110 + 1):
            bad = bytearray(plain)
            bad[offset] = 0x7a
            invalid.append(bytes(bad))
        # Explicit name padding byte in name "init" (110+5 -> aligned to 116).
        padded_name = bytearray(cpio_fixture())
        padded_name[115] = 1
        invalid.append(bytes(padded_name))
        for blob in invalid:
            with self.subTest(blob=blob[:20]), self.assertRaises(ValueError):
                parse_newc(blob)

    def test_gzip_crc_truncation_concatenation_and_bomb_limit(self):
        compressed = ramdisk_fixture()
        damaged = bytearray(compressed)
        damaged[-8] ^= 1
        for blob in (compressed[:-1], compressed + b"\0", compressed + compressed, bytes(damaged)):
            with self.subTest(length=len(blob)), self.assertRaises(ValueError):
                parse_ramdisk(blob)
        with self.assertRaises(ValueError):
            gzip_member(gzip.compress(bytes(10000), mtime=0), 128)


if __name__ == "__main__":
    unittest.main()
