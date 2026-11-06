#!/usr/bin/env python3
"""End-to-end artifact checks using synthetic, non-executed AArch64 fixtures."""
import contextlib
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock

import artifact_check as check
import image_formats
import pack_boot
import validate
from image_formats import parse_newc, parse_static_aarch64_elf
from test_board import minimum_board
from test_pack import arm64_fixture, cpio_entry, cpio_fixture, dtb_fixture


def static_elf_fixture(*, interp=False):
    """A structurally valid ELF64 AArch64 executable, not executable test code."""
    binary = bytearray(256)
    binary[:16] = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into("<HHIQQQIHHHHHH", binary, 16, 2, 183, 1, 0x400080,
                     64, 0, 0, 64, 56, 2 if interp else 1, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", binary, 64, 1, 5, 0, 0x400000, 0,
                     len(binary), len(binary), 0x1000)
    if interp:
        struct.pack_into("<IIQQQQQQ", binary, 120, 3, 4, 192, 0, 0, 16, 16, 1)
        binary[192:208] = b"/lib/ld-test.so\0\0"
    return bytes(binary)


def board_fixture():
    # Use the board-policy unit fixture; these setdefaults also permit running
    # while the Lead upgrades test_board.py to the new revision in parallel.
    nodes = copy.deepcopy(minimum_board())
    nodes["/"].update({
        "compatible": b"oppo,pcam00\0qcom,sdm710\0qcom,sdm670\0",
        "oppo,ramdiag-revision": (check.REVISION + "\0").encode(),
        "qcom,msm-id": struct.pack(">4I", 360, 0, 393, 0),
        "qcom,pmic-id": struct.pack(">12I", 0x1001b, 0x1011a, 0, 0,
                                    0x1001b, 0x2001a, 0, 0, 0x1001b, 0x202001a, 0, 0),
        "#address-cells": struct.pack(">I", 2), "#size-cells": struct.pack(">I", 2),
    })
    for name, props in nodes.items():
        if name.startswith("/reserved-memory/"):
            props["no-map"] = b""
    phy = nodes["/soc@0/phy@88e2000"]
    for number, prop, low, high in ((101, "vdd-supply", 880000, 900000),
                                   (102, "vdda-pll-supply", 1800000, 1800000),
                                   (103, "vdda-phy-dpdm-supply", 3088000, 3100000)):
        phy[prop] = struct.pack(">I", number)
        nodes[f"/fixture-regulator-{number}"] = {
            "phandle": struct.pack(">I", number),
            "regulator-min-microvolt": struct.pack(">I", low),
            "regulator-max-microvolt": struct.pack(">I", high),
        }
    return dtb_fixture(nodes)


# Keep the synthetic release and alias set independent of checker policy so a
# removed requirement cannot silently remove its corresponding positive fixture.
FIXTURE_RELEASE = b"7.2.3-oppo-pcam00-b-ramdiag-v4+"
FIXTURE_APPLETS = (
    "sh", "mount", "mkdir", "cat", "dmesg", "ip", "httpd", "sleep", "reboot",
    "ln", "chmod", "cp", "mv", "timeout", "setsid", "kill",
)


def config_fixture():
    # Generated .config envelope plus maintained policy and architecture selector.
    # Policy validation is real, not mocked; this remains a synthetic non-kernel.
    root = Path(__file__).resolve().parent.parent
    header = b"#\n# Automatically generated file; DO NOT EDIT.\n# Linux/arm64 7.2.3 Kernel Configuration\n#\n"
    return header + (root / "config/ramdiag.config").read_bytes() + b"\nCONFIG_ARM64=y\n"


def ikconfig_fixture(config):
    return b"IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED"


def kernel_image_fixture(config=None, *, release=FIXTURE_RELEASE, envelope=None, banner=None):
    config = config_fixture() if config is None else config
    if envelope is None:
        envelope = ikconfig_fixture(config)
    if banner is None:
        banner = (b"Linux version " + release
                  + b" (fixture@invalid) (synthetic compiler) #1 SMP PREEMPT fixture\n\0")
    body = envelope + b"\0" + banner
    return arm64_fixture(64 + len(body))[:64] + body


class ArtifactFixture:
    def __init__(self, root):
        self.root = root
        self.source = root / "source"
        self.artifacts = root / "artifacts"
        (self.source / "initramfs").mkdir(parents=True)
        (self.source / "scripts").mkdir()
        (self.source / "docs/deep").mkdir(parents=True)
        self.artifacts.mkdir()
        (self.source / "REVISION").write_bytes((check.REVISION + "\n").encode())
        (self.source / "README.md").write_text("Synthetic source, never booted.\n")
        (self.source / "docs/deep/audit.txt").write_text("recursive fixture\n")
        self.source_files = {"init": b"#!/bin/sh\n# synthetic fixture, do not execute\n",
                             "safety.sh": b"# synthetic safety helper\n",
                             "runtime.sh": b"# synthetic runtime helper\n",
                             "usb.sh": b"# synthetic USB helper\n"}
        for name, data in self.source_files.items():
            (self.source / "initramfs" / name).write_bytes(data)
        for module in (check, image_formats, pack_boot, validate):
            path = Path(module.__file__)
            (self.source / "scripts" / path.name).write_bytes(path.read_bytes())
        self.entries = {
            "bin": (stat.S_IFDIR | 0o755, b"", 0, 0),
            "dev": (stat.S_IFDIR | 0o755, b"", 0, 0),
            "etc": (stat.S_IFDIR | 0o755, b"", 0, 0),
            "init": (stat.S_IFREG | 0o755, self.source_files["init"], 0, 0),
            "bin/busybox": (stat.S_IFREG | 0o755, static_elf_fixture(), 0, 0),
            "dev/console": (stat.S_IFCHR | 0o600, b"", 5, 1),
            "dev/null": (stat.S_IFCHR | 0o666, b"", 1, 3),
            "etc/recovery-seconds": (stat.S_IFREG | 0o644, b"180\n", 0, 0),
            "etc/ramdiag-revision": (stat.S_IFREG | 0o644, (check.REVISION + "\n").encode(), 0, 0),
        }
        for applet in FIXTURE_APPLETS:
            self.entries["bin/" + applet] = (stat.S_IFLNK | 0o777, b"busybox", 0, 0)
        for name, target in check.HELPER_MAP.items():
            self.entries[target] = (stat.S_IFREG | 0o644, self.source_files[name], 0, 0)
        self.config = config_fixture()
        self.image = kernel_image_fixture(self.config)
        self.dtb = board_fixture()
        (self.artifacts / "Image").write_bytes(self.image)
        (self.artifacts / check.DTB_NAME).write_bytes(self.dtb)
        (self.artifacts / "kernel.config").write_bytes(self.config)
        (self.artifacts / "kernelrelease.txt").write_bytes(FIXTURE_RELEASE + b"\n")
        self.write_ramdisk()

    def cpio(self):
        records = [cpio_entry(name, mode, body, major=major, minor=minor, ino=index)
                   for index, (name, (mode, body, major, minor)) in enumerate(sorted(self.entries.items()), 1)]
        return cpio_fixture(records)

    def parsed_entries(self):
        return parse_newc(self.cpio())

    def write_ramdisk(self):
        ramdisk = gzip.compress(self.cpio(), mtime=0)
        (self.artifacts / check.RAMDISK_NAME).write_bytes(ramdisk)
        boot, kernel = pack_boot.pack(self.image, self.dtb, ramdisk)
        (self.artifacts / check.BOOT_NAME).write_bytes(boot)
        manifest = pack_boot.boot_manifest(boot, kernel)
        (self.artifacts / Path(check.BOOT_NAME).with_suffix(".json")).write_text(json.dumps(manifest))
        return boot

    def write_image(self, image):
        self.image = image
        (self.artifacts / "Image").write_bytes(image)
        return self.write_ramdisk()

    def write_source_manifest(self):
        manifest = {"revision": check.REVISION, "files": check.source_inventory(self.source)}
        (self.artifacts / "source-manifest.json").write_text(json.dumps(manifest))
        return manifest


class BootParserTests(unittest.TestCase):
    def setUp(self):
        self.ramdisk = gzip.compress(cpio_fixture(), mtime=0)
        self.image = arm64_fixture()
        self.dtb = dtb_fixture()
        self.boot, self.kernel = pack_boot.pack(self.image, self.dtb, self.ramdisk)

    def test_valid_and_all_extractions(self):
        parsed = check.parse_boot_v1(self.boot)
        self.assertEqual(parsed.image, self.image)
        self.assertEqual(parsed.dtb, self.dtb)
        self.assertEqual(parsed.ramdisk, self.ramdisk)
        self.assertEqual(parsed.cpio, cpio_fixture())
        self.assertEqual(parsed.cmdline, check.EXPECTED_CMDLINE)

    def test_geometry_version_sizes_and_dtbo_rejected(self):
        fields = [(0, b"X"), (12, struct.pack("<I", 0x8001)), (20, struct.pack("<I", 0)),
                  (24, struct.pack("<I", 1)), (28, struct.pack("<I", 1)),
                  (32, struct.pack("<I", 0)), (36, struct.pack("<I", 2048)),
                  (40, struct.pack("<I", 2)), (44, struct.pack("<I", 1)),
                  (8, struct.pack("<I", 0xffffffff)), (16, struct.pack("<I", 0)),
                  (1632, struct.pack("<I", 4)), (1636, struct.pack("<Q", 4096)),
                  (1644, struct.pack("<I", 1649))]
        for offset, value in fields:
            damaged = bytearray(self.boot)
            damaged[offset:offset + len(value)] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                check.parse_boot_v1(bytes(damaged))

    def test_all_padding_regions_and_digest_rejected(self):
        rd_start = check.PAGE + ((len(self.kernel) + check.PAGE - 1) // check.PAGE) * check.PAGE
        for offset in (61, 64 + len(check.EXPECTED_CMDLINE) + 1, 596, 607, 608,
                       1631, 1648, 4095, check.PAGE + len(self.kernel),
                       rd_start + len(self.ramdisk), 576, check.PAGE + 15):
            damaged = bytearray(self.boot)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                check.parse_boot_v1(bytes(damaged))

    def test_unterminated_nonprintable_cmdline(self):
        for command in (b"x" * 512, b"x\x7f" + bytes(510), b"x\ny" + bytes(509)):
            damaged = bytearray(self.boot)
            damaged[64:576] = command
            with self.assertRaises(ValueError):
                check.parse_boot_v1(bytes(damaged))

    def test_truncation_extra_page_signature(self):
        for value in (self.boot[:10], self.boot[:4096], self.boot[:-1], self.boot[:-4096],
                      self.boot + b"signature", self.boot + bytes(4096)):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                check.parse_boot_v1(value)


class ElfTests(unittest.TestCase):
    def test_valid_without_execution(self):
        report = parse_static_aarch64_elf(static_elf_fixture())
        self.assertEqual(report["machine"], "AArch64")
        self.assertFalse(report["pt_interp"])
        self.assertFalse(report["executed"])

    def test_wrong_machine_interp_truncation_bounds(self):
        for offset, payload in ((18, struct.pack("<H", 62)), (4, b"\x01"),
                                (32, struct.pack("<Q", 256)), (54, struct.pack("<H", 55)),
                                (96, struct.pack("<Q", 10000)), (24, struct.pack("<Q", 1))):
            binary = bytearray(static_elf_fixture())
            binary[offset:offset + len(payload)] = payload
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                parse_static_aarch64_elf(bytes(binary))
        with self.assertRaises(ValueError):
            parse_static_aarch64_elf(static_elf_fixture(interp=True))
        for length in (0, 63, 64, 119, 255):
            with self.subTest(length=length), self.assertRaises(ValueError):
                parse_static_aarch64_elf(static_elf_fixture()[:length])

    def test_static_pie_allowed_but_needed_rejected(self):
        binary = bytearray(static_elf_fixture())
        struct.pack_into("<H", binary, 16, 3)
        struct.pack_into("<H", binary, 56, 2)
        struct.pack_into("<IIQQQQQQ", binary, 120, 2, 4, 192, 0x4000c0, 0, 32, 32, 8)
        struct.pack_into("<qQqQ", binary, 192, 0, 0, 0, 0)
        self.assertFalse(parse_static_aarch64_elf(bytes(binary))["dynamic_dependencies"])
        struct.pack_into("<qQ", binary, 192, 1, 1)
        with self.assertRaises(ValueError):
            parse_static_aarch64_elf(bytes(binary))


class KernelImageTests(unittest.TestCase):
    def setUp(self):
        self.config = config_fixture()
        self.release = FIXTURE_RELEASE + b"\n"
        self.image = kernel_image_fixture(self.config)

    def verify(self, image, config=None, release=None):
        return check.verify_kernel_image(image, self.config if config is None else config,
                                         self.release if release is None else release)

    def test_valid_ikconfig_exact_bytes_and_release_string_evidence(self):
        result = self.verify(self.image)
        self.assertTrue(result["embedded_config_byte_equal"])
        self.assertTrue(result["release_matches_image"])
        self.assertEqual(result["kernelrelease"], FIXTURE_RELEASE.decode())
        self.assertTrue(result["linux_version"]["nul_terminated"])
        self.assertEqual(result["embedded_config"]["sha256"], check.digest(self.config))
        self.assertTrue(result["embedded_config"]["gzip_crc_isize_checked"])
        self.assertEqual(result["embedded_config"]["max_output"], 2 * 1024 * 1024)
        self.assertEqual(result["embedded_config"]["CONFIG_IKCONFIG"], "y")
        self.assertFalse(result["complete_code_verification"])
        self.assertIn("evidence only", result["warning"])
        # The real banner's trailing LF is retained, not stripped at runtime.
        self.assertTrue(result["linux_version"]["text"].endswith("\n"))
        binary_boundary = bytearray(self.image)
        binary_boundary[binary_boundary.find(b"Linux version ") - 1] = 0xff
        self.assertTrue(self.verify(bytes(binary_boundary))["release_matches_image"])
        for release in (FIXTURE_RELEASE[:-1], b"7.3-rc2-oppo-pcam00-b-ramdiag-v4+"):
            with self.subTest(release=release):
                self.assertTrue(self.verify(kernel_image_fixture(release=release),
                                            release=release + b"\n")["release_matches_image"])

    def test_image_bounds_are_checked_before_marker_search(self):
        for image in (b"", self.image[:63], self.image[:56] + b"NOPE" + self.image[60:]):
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                self.verify(image)
        with mock.patch.object(image_formats, "MAX_IMAGE", len(self.image) - 1):
            with self.assertRaisesRegex(ValueError, "Image length"):
                self.verify(self.image)

    def test_missing_duplicate_and_reordered_ikconfig_markers_rejected(self):
        compressed = gzip.compress(self.config, mtime=0)
        good = ikconfig_fixture(self.config)
        bad = (b"", good.replace(b"IKCFG_ST", b"IKCFG_XX"),
               good.replace(b"IKCFG_ED", b"IKCFG_XX"), good + good,
               good + b"IKCFG_ST", b"IKCFG_ED" + good,
               b"IKCFG_ED" + compressed + b"IKCFG_ST", b"IKCFG_STIKCFG_ED")
        for envelope in bad:
            with self.subTest(size=len(envelope)), self.assertRaisesRegex(ValueError, "IKCFG|IKCONFIG"):
                self.verify(kernel_image_fixture(envelope=envelope))

    def test_embedded_gzip_crc_isize_truncation_concatenation_and_padding(self):
        compressed = gzip.compress(self.config, mtime=0)
        bad = [b"not gzip", compressed[:-1], compressed[:-8],
               compressed + compressed, compressed + b"\0", b"\0" + compressed]
        for offset in (-8, -4):
            broken = bytearray(compressed)
            broken[offset] ^= 1
            bad.append(bytes(broken))
        for member in bad:
            with self.subTest(size=len(member)), self.assertRaisesRegex(ValueError, "gzip"):
                self.verify(kernel_image_fixture(envelope=b"IKCFG_ST" + member + b"IKCFG_ED"))

    def test_ikconfig_output_limit_and_bounded_compressed_input(self):
        self.assertEqual(check.IKCONFIG_LIMIT, 2 * 1024 * 1024)
        # Valid generated text at the exact output boundary is accepted; one
        # extra byte must fail inside the gzip decoder, not after inflation.
        suffix_size = check.IKCONFIG_LIMIT - len(self.config)
        at_limit = self.config + b"#" + b"x" * (suffix_size - 2) + b"\n"
        self.assertTrue(self.verify(kernel_image_fixture(at_limit), config=at_limit)["embedded_config_byte_equal"])
        above_limit = at_limit + b"\n"
        with self.assertRaisesRegex(ValueError, "decompression limit"):
            self.verify(kernel_image_fixture(above_limit), config=above_limit)
        compressed = gzip.compress(self.config, mtime=0)
        with mock.patch.object(check, "METADATA_LIMIT", len(compressed) - 1):
            with self.assertRaisesRegex(ValueError, "envelope bounds"):
                self.verify(self.image)

    def test_exact_configuration_equality_never_normalizes(self):
        for different in (self.config + b"\n", self.config.replace(b"\n", b"\r\n"),
                          self.config.replace(b"CONFIG_LOG_BUF_SHIFT=19", b"CONFIG_LOG_BUF_SHIFT=20")):
            with self.subTest(size=len(different)), self.assertRaisesRegex(ValueError, "differs byte-for-byte"):
                self.verify(self.image, config=different)
        other_embedded = self.config.replace(b"CONFIG_LOG_BUF_SHIFT=19", b"CONFIG_LOG_BUF_SHIFT=20")
        with self.assertRaisesRegex(ValueError, "differs byte-for-byte"):
            self.verify(kernel_image_fixture(other_embedded))

    def test_valid_gzip_must_hold_generated_config_text_with_ikconfig_builtin(self):
        invalid = [b"arbitrary text\n", self.config.replace(b"Linux/arm64", b"Linux/x86"),
                   self.config.replace(b"# Automatically generated file; DO NOT EDIT.", b"# fake header"),
                   self.config[:-1], self.config.replace(b"\n", b"\r\n"),
                   self.config + b"\0\n", self.config + b"# \xff\n",
                   self.config + b"echo unparsed shell\n", self.config + b"CONFIG_BROKEN=\n",
                   self.config + b"CONFIG_IKCONFIG=y\n",
                   self.config + b"# CONFIG_IKCONFIG is not set\n",
                   self.config.replace(b"CONFIG_IKCONFIG=y\n", b""),
                   self.config.replace(b"CONFIG_IKCONFIG=y", b"CONFIG_IKCONFIG=m"),
                   self.config.replace(b"CONFIG_IKCONFIG=y", b"# CONFIG_IKCONFIG is not set"),
                   self.config.replace(b"pcam00-b-ramdiag-v4", b"pcam00-b-ramdiag-v3")]
        for index, config in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.verify(kernel_image_fixture(config), config=config)
        # Legitimate strings, lowercase Kconfig symbols and escaped quotes must
        # not be mistaken for malformed lines or run as commands.
        config = (self.config + b'CONFIG_CC_VERSION_TEXT="cc \\"fixture\\""\n'
                  + b'CONFIG_CMDLINE="$(do-not-run); with spaces"\n'
                  + b"CONFIG_TEST_lower=0x0A\nCONFIG_TEST_NEG=-1\nCONFIG_TEST_EMPTY=\"\"\n")
        self.assertTrue(self.verify(kernel_image_fixture(config), config=config)["embedded_config_byte_equal"])

    def test_release_line_length_fixed_suffix_and_no_whitespace_stripping(self):
        bad = (b"", FIXTURE_RELEASE, self.release + b"\n", b" " + self.release,
               FIXTURE_RELEASE + b" \n", FIXTURE_RELEASE + b"\r\n", FIXTURE_RELEASE + b"\0\n",
               b"9" * 65 + b"\n", b"7.2.3\n", b"7.2.3-oppo-pcam00-b-ramdiag-v3+\n",
               FIXTURE_RELEASE + b"-other\n", FIXTURE_RELEASE + b"+\n",
               b"prefix-" + self.release, b"7.2.3-oppo-pcam00-b-ramdiag-v40\n")
        for release in bad:
            with self.subTest(release=release), self.assertRaisesRegex(ValueError, "kernelrelease.txt"):
                self.verify(self.image, release=release)
        suffix = b".0.0-oppo-pcam00-b-ramdiag-v4+\n"
        boundary = b"9" * (65 - len(suffix)) + suffix
        self.assertEqual(len(boundary), 65)
        self.assertEqual(check.kernel_release(boundary), boundary[:-1].decode())

    def test_linux_version_exact_prefix_terminator_unique_and_line_bounds(self):
        prefix = b"Linux version " + FIXTURE_RELEASE + b" "
        good = prefix + b"fixture\n\0"
        bad = (b"", b"unrelated " + FIXTURE_RELEASE + b"\0", good[:-1],
               good + b"\0" + good + b"\0" + good, b"prefix " + good,
               b"Linux version 7.2.4-oppo-pcam00-b-ramdiag-v4+ fixture\0",
               b"Linux version " + FIXTURE_RELEASE + b"-different fixture\0",
               b"Linux version " + FIXTURE_RELEASE + b"\0", prefix + b"\0",
               prefix + b"fixture\nextra\0", prefix + b"fixture\r\n\0",
               prefix + b"\xff\0", prefix + b"\tfixture\0",
               prefix + b"x" * (check.LINUX_VERSION_LIMIT + 1 - len(prefix)) + b"\0")
        for index, banner in enumerate(bad):
            with self.subTest(case=index), self.assertRaisesRegex(ValueError, "Linux version"):
                self.verify(kernel_image_fixture(banner=banner))
        boundary = prefix + b"x" * (check.LINUX_VERSION_LIMIT - len(prefix)) + b"\0"
        self.assertTrue(self.verify(kernel_image_fixture(banner=boundary))["release_matches_image"])

    def test_weak_and_final_banners_both_must_match(self):
        prefix=b'Linux version '+FIXTURE_RELEASE+b' '
        weak=prefix+b'(fixture) # SMP buildtime\n\0'
        final=prefix+b'(fixture) #1 SMP buildtime\n\0'
        report=self.verify(kernel_image_fixture(banner=weak+b'\0'+final))
        self.assertEqual(report['linux_version']['occurrence_count'],2)
        self.assertTrue(report['linux_version']['all_match_release'])
        self.assertFalse(report['linux_version']['runtime_canonical_symbol_identified'])
        old=final.replace(FIXTURE_RELEASE,b'7.2.3-oppo-pcam00-b-ramdiag-v3+')
        for invalid in (weak+b'\0'+old,old+b'\0'+final,weak+b'\0'+final[:-1],
                        weak+b'\0'+final+b'\0'+final):
            with self.subTest(banner=invalid),self.assertRaises(ValueError):
                self.verify(kernel_image_fixture(banner=invalid))

    def test_release_decoy_in_gzip_header_is_not_image_banner(self):
        # FNAME is authenticated only as gzip framing, not as a kernel banner.
        # CRC still validates, and the config bytes still equal kernel.config.
        member = bytearray(gzip.compress(self.config, mtime=0))
        member[3] = 8  # FNAME
        member[9] = 0  # ensure a decoy's previous byte is NUL too
        banner = b"Linux version " + FIXTURE_RELEASE + b" fixture\0"
        member = bytes(member[:10]) + banner + bytes(member[10:])
        with self.assertRaisesRegex(ValueError, "outside IKCONFIG"):
            self.verify(kernel_image_fixture(envelope=b"IKCFG_ST" + member + b"IKCFG_ED", banner=b""))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ArtifactFixture(Path(self.temp.name))
        self.artifacts, self.source = self.fixture.artifacts, self.fixture.source

    def test_full_positive_and_no_source_authentication_claim(self):
        report = check.verify_artifacts(self.artifacts, self.source)
        self.assertEqual(report["result"], "PASS")
        self.assertFalse(report["hardware_tested"])
        self.assertFalse(report["target_code_executed"])
        self.assertEqual(report["revision"], "pcam00-b-ramdiag-v4")
        self.assertEqual(report["config"]["result"], "PASS")
        self.assertTrue(report["kernel_image"]["embedded_config_byte_equal"])
        self.assertTrue(report["kernel_image"]["release_matches_image"])
        self.assertFalse(report["kernel_image"]["complete_code_verification"])
        self.assertEqual(report["kernel_image"]["kernelrelease"], FIXTURE_RELEASE.decode())
        self.assertEqual(report["files"]["kernelrelease.txt"], check.file_record(FIXTURE_RELEASE + b"\n"))
        self.assertEqual(report["device_tree"]["result"], "PASS")
        self.assertEqual(report["source_manifest"]["result"], "NOT_VERIFIED")
        self.assertFalse(report["source_manifest"]["source_identity_authenticated"])
        self.assertTrue(report["boot"]["in_memory_repack_byte_equal"])
        self.assertEqual(report["initramfs"]["recovery_seconds"], 180)
        aliases = report["initramfs"]["required_applet_symlinks"]
        self.assertEqual(aliases["aliases"], {"bin/" + applet: "busybox" for applet in FIXTURE_APPLETS})
        self.assertTrue(aliases["target_elf_verified"])
        self.assertFalse(aliases["runtime_applets_verified"])
        self.assertEqual(set(check.REQUIRED_APPLETS), set(FIXTURE_APPLETS))

    def test_consistent_boot_repack_cannot_substitute_for_actual_image_evidence(self):
        different = self.fixture.config.replace(b"CONFIG_LOG_BUF_SHIFT=19", b"CONFIG_LOG_BUF_SHIFT=20")
        broken_member = bytearray(gzip.compress(self.fixture.config, mtime=0))
        broken_member[-8] ^= 1
        images = [arm64_fixture(), kernel_image_fixture(different),
                  kernel_image_fixture(envelope=b"IKCFG_ST" + bytes(broken_member) + b"IKCFG_ED"),
                  kernel_image_fixture(release=b"7.2.3-oppo-pcam00-b-ramdiag-v3+"),
                  kernel_image_fixture(banner=b"some unrelated string\0")]
        for image in images:
            # Update Image, boot SHA1, gzip, all padding and manifest together:
            # structural/repack success must not make the bad evidence pass.
            self.fixture.write_image(image)
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                check.verify_artifacts(self.artifacts, self.source)

    def test_cli_uses_real_release_file_and_never_writes_on_evidence_failure(self):
        argv = ["--artifacts", str(self.artifacts), "--source-root", str(self.source)]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(check.main(argv), 0)
        report = json.loads(stdout.getvalue())
        self.assertTrue(report["kernel_image"]["embedded_config_byte_equal"])
        self.assertTrue(report["kernel_image"]["release_matches_image"])
        path = self.artifacts / "kernelrelease.txt"
        output = self.artifacts / "failed-evidence.json"
        for value in (b"7.2.4-oppo-pcam00-b-ramdiag-v4+\n", FIXTURE_RELEASE + b" \n", b"X" * 100):
            path.write_bytes(value)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(check.main(argv + ["--output", str(output)]), 1)
            self.assertFalse(output.exists())
        # Missing or symlinked release metadata must not fall back to .config.
        path.unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main(argv + ["--output", str(output)]), 1)
        path.symlink_to("kernel.config")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main(argv + ["--output", str(output)]), 1)
        self.assertFalse(output.exists())

    def test_standalone_config_claim_cannot_replace_embedded_bytes(self):
        path = self.artifacts / "kernel.config"
        path.write_bytes(self.fixture.config + b"\n")
        with self.assertRaisesRegex(ValueError, "differs byte-for-byte"):
            check.verify_artifacts(self.artifacts, self.source)
        config = self.fixture.config.replace(b"CONFIG_IKCONFIG=y", b"# CONFIG_IKCONFIG is not set")
        path.write_bytes(config)
        self.fixture.write_image(kernel_image_fixture(config))
        with self.assertRaisesRegex(ValueError, "CONFIG_IKCONFIG must be y"):
            check.verify_artifacts(self.artifacts, self.source)

    def test_actual_archive_writer_with_source_helpers_without_execution(self):
        reviewed_root = Path(__file__).resolve().parent.parent
        writer_path = reviewed_root / "initramfs/mkcpio.py"
        spec = importlib.util.spec_from_file_location("fixture_mkcpio", writer_path)
        writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(writer)  # archive encoder only; its __main__ is not run
        root = self.fixture.root / "ramroot"
        for name in ("bin", "dev", "etc"):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / "bin/busybox").write_bytes(static_elf_fixture())
        (root / "bin/busybox").chmod(0o755)
        for applet in FIXTURE_APPLETS:
            (root / "bin" / applet).symlink_to("busybox")
        (root / "init").write_bytes((reviewed_root / "initramfs/init").read_bytes())
        (root / "init").chmod(0o755)
        for source, target in check.HELPER_MAP.items():
            path = reviewed_root / "initramfs" / source
            if path.exists():
                (root / target).write_bytes(path.read_bytes())
                (root / target).chmod(0o644)
        (root / "etc/ramdiag-revision").write_bytes((reviewed_root / "REVISION").read_bytes())
        (root / "etc/recovery-seconds").write_bytes(b"180\n")
        encoded = io.BytesIO()
        writer.archive(root, 1793404800, encoded)
        entries = parse_newc(encoded.getvalue())
        result = check.verify_initramfs(entries, reviewed_root)
        self.assertEqual(result["result"], "PASS")
        self.assertFalse(result["init_executed"])

    def test_recursive_source_manifest_comparison(self):
        self.fixture.write_source_manifest()
        result = check.verify_artifacts(self.artifacts, self.source)["source_manifest"]
        self.assertEqual(result["result"], "PASS")
        self.assertTrue(result["recursive_hashes_match_supplied_source"])
        self.assertFalse(result["source_identity_authenticated"])
        (self.source / "docs/deep/audit.txt").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
            check.verify_artifacts(self.artifacts, self.source)

    def test_downloaded_source_copy_is_compared_not_assumed(self):
        self.fixture.write_source_manifest()
        copied = self.artifacts / "sources"
        for relative in check.source_inventory(self.source):
            target = copied / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.source / relative).read_bytes())
        report = check.verify_source_manifest(self.artifacts, self.source)
        self.assertEqual(report["downloaded_sources"]["result"], "PASS")
        (copied / "docs/deep/audit.txt").write_bytes(b"tampered copy")
        with self.assertRaisesRegex(ValueError, "Downloaded sources"):
            check.verify_source_manifest(self.artifacts, self.source)
        (self.artifacts / "source-manifest.json").unlink()
        report = check.verify_source_manifest(self.artifacts, self.source)
        self.assertEqual(report["result"], "NOT_VERIFIED")
        self.assertEqual(report["downloaded_sources"]["result"], "NOT_VERIFIED")
        self.assertFalse(report["source_identity_authenticated"])

    def test_manifest_missing_extra_bad_path_duplicate_json(self):
        good = self.fixture.write_source_manifest()
        invalid = []
        missing = copy.deepcopy(good)
        missing["files"].pop("docs/deep/audit.txt")
        invalid.append(missing)
        wrong = copy.deepcopy(good)
        wrong["files"]["../escape"] = next(iter(wrong["files"].values()))
        invalid.append(wrong)
        wrong = copy.deepcopy(good)
        wrong["files"]["scripts/pack_boot.py"]["sha256"] = "0" * 64
        invalid.append(wrong)
        wrong = copy.deepcopy(good)
        wrong["revision"] = "old-run3"
        invalid.append(wrong)
        wrong = copy.deepcopy(good)
        wrong["source_commit"] = "artifact-defined-extra-field"
        invalid.append(wrong)
        wrong = copy.deepcopy(good)
        wrong["files"]["scripts/pack_boot.py"]["command"] = "DO NOT EXECUTE"
        invalid.append(wrong)
        for record in invalid:
            (self.artifacts / "source-manifest.json").write_text(json.dumps(record))
            with self.subTest(record=record.get("revision")), self.assertRaises(ValueError):
                check.verify_source_manifest(self.artifacts, self.source)
        (self.artifacts / "source-manifest.json").write_text('{"files":{},"files":{}}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            check.verify_source_manifest(self.artifacts, self.source)

    def test_source_inventory_rejects_new_missing_and_symlinked_files(self):
        self.fixture.write_source_manifest()
        new_path = self.source / "docs/deep/new.txt"
        new_path.write_text("not listed\n")
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            check.verify_source_manifest(self.artifacts, self.source)
        new_path.unlink()
        (self.source / "docs/link.txt").symlink_to("deep/audit.txt")
        with self.assertRaises(ValueError):
            check.verify_source_manifest(self.artifacts, self.source)
        (self.source / "initramfs/unknown.sh").write_text("# not mapped\n")
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            check.verify_initramfs(self.fixture.parsed_entries(), self.source)

    def test_source_code_hashes_must_match_running_verifier(self):
        # A manifest agreeing with a modified copy is not evidence that the
        # reviewed verifier was run, and must never cause that copy to import.
        (self.source / "scripts/pack_boot.py").write_text("raise RuntimeError('DO NOT EXECUTE')\n")
        self.fixture.write_source_manifest()
        with self.assertRaisesRegex(ValueError, "running trusted verifier"):
            check.verify_artifacts(self.artifacts, self.source)

    def test_artifact_metadata_cannot_replace_code_or_report_truth(self):
        before = Path(pack_boot.__file__).read_bytes()
        manifest = self.fixture.write_source_manifest()
        manifest.update({"source_root": "/", "module": "evil", "hardware_tested": True,
                         "output": str(Path(pack_boot.__file__)), "command": "DO NOT EXECUTE"})
        (self.artifacts / "source-manifest.json").write_text(json.dumps(manifest))
        (self.artifacts / "pack_boot.py").write_text("raise RuntimeError('DO NOT IMPORT')\n")
        with self.assertRaisesRegex(ValueError, "source-manifest fields"):
            check.verify_artifacts(self.artifacts, self.source)
        self.fixture.write_source_manifest()
        report = check.verify_artifacts(self.artifacts, self.source)
        self.assertFalse(report["hardware_tested"])
        self.assertFalse(report["device_operations_performed"])
        self.assertEqual(Path(pack_boot.__file__).read_bytes(), before)

    def test_init_helper_revision_and_timer_byte_mismatches(self):
        originals = dict(self.fixture.entries)
        changes = [
            ("init", (stat.S_IFREG | 0o755, b"#!/bin/sh\n# old init\n", 0, 0)),
            ("init", (stat.S_IFREG | 0o644, self.fixture.source_files["init"], 0, 0)),
            ("etc/ramdiag-safety.sh", (stat.S_IFREG | 0o644, b"old helper", 0, 0)),
            ("etc/ramdiag-runtime.sh", (stat.S_IFREG | 0o644, b"old helper", 0, 0)),
            ("etc/ramdiag-usb.sh", (stat.S_IFREG | 0o644, b"old helper", 0, 0)),
            ("etc/ramdiag-revision", (stat.S_IFREG | 0o644, b"run3\n", 0, 0)),
            ("etc/recovery-seconds", (stat.S_IFREG | 0o644, b"0\n", 0, 0)),
            ("etc/recovery-seconds", (stat.S_IFREG | 0o644, b"180\x00\n", 0, 0)),
            ("dev/console", (stat.S_IFCHR | 0o600, b"", 1, 1)),
            ("dev/null", (stat.S_IFREG | 0o666, b"", 0, 0)),
            ("bin/busybox", (stat.S_IFREG | 0o755, static_elf_fixture(interp=True), 0, 0)),
        ]
        for name, value in changes:
            self.fixture.entries = {**originals, name: value}
            with self.subTest(name=name), self.assertRaises(ValueError):
                check.verify_initramfs(self.fixture.parsed_entries(), self.source)
        self.fixture.entries = originals

    def test_each_core_applet_must_be_present_as_direct_busybox_symlink(self):
        original = dict(self.fixture.entries)
        for applet in FIXTURE_APPLETS:
            name = "bin/" + applet
            candidates = (None, (stat.S_IFREG | 0o755, static_elf_fixture(), 0, 0),
                          (stat.S_IFLNK | 0o777, b"../bin/busybox", 0, 0),
                          (stat.S_IFLNK | 0o777, b"./busybox", 0, 0),
                          (stat.S_IFLNK | 0o777, b"other", 0, 0),
                          (stat.S_IFLNK | 0o777, b"alias", 0, 0))
            for replacement in candidates:
                self.fixture.entries = {**original,
                                        "bin/other": (stat.S_IFREG | 0o755, static_elf_fixture(), 0, 0),
                                        "bin/alias": (stat.S_IFLNK | 0o777, b"busybox", 0, 0)}
                if replacement is None:
                    self.fixture.entries.pop(name)
                else:
                    self.fixture.entries[name] = replacement
                # Every mutated archive remains safe for the generic parser;
                # it is specifically the core alias policy that must reject it.
                with self.subTest(applet=applet, alias=replacement), self.assertRaisesRegex(ValueError, name):
                    check.verify_initramfs(self.fixture.parsed_entries(), self.source)
        self.fixture.entries = original

    def test_missing_or_wrong_core_applet_rejects_consistently_repacked_archive(self):
        original = dict(self.fixture.entries)
        for replacement in (None, (stat.S_IFLNK | 0o777, b"sh", 0, 0),
                            (stat.S_IFREG | 0o755, static_elf_fixture(), 0, 0)):
            self.fixture.entries = dict(original)
            if replacement is None:
                self.fixture.entries.pop("bin/timeout")
            else:
                self.fixture.entries["bin/timeout"] = replacement
            self.fixture.write_ramdisk()
            with self.subTest(alias=replacement), self.assertRaisesRegex(ValueError, "bin/timeout"):
                check.verify_artifacts(self.artifacts, self.source)

    def test_core_alias_targets_must_be_the_valid_static_aarch64_elf(self):
        original = dict(self.fixture.entries)
        for bad in (b"not an elf", static_elf_fixture(interp=True)):
            self.fixture.entries = {**original, "bin/busybox": (stat.S_IFREG | 0o755, bad, 0, 0)}
            self.fixture.write_ramdisk()
            with self.assertRaises(ValueError):
                check.verify_artifacts(self.artifacts, self.source)

    def test_missing_new_helpers_rejects_old_cpio(self):
        for name in ("etc/ramdiag-revision", "etc/ramdiag-runtime.sh", "etc/ramdiag-usb.sh"):
            entries = self.fixture.parsed_entries()
            entries.pop(name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                check.verify_initramfs(entries, self.source)
        # Full artifact verification must reject an internally consistent,
        # correctly SHA1/manifest-repacked old initramfs too.
        self.fixture.entries.pop("etc/ramdiag-revision")
        self.fixture.write_ramdisk()
        with self.assertRaises(ValueError):
            check.verify_artifacts(self.artifacts, self.source)

    def test_missing_and_unmapped_source_helpers(self):
        entries = self.fixture.parsed_entries()
        (self.source / "initramfs/runtime.sh").unlink()
        with self.assertRaisesRegex(ValueError, "counterpart"):
            check.verify_initramfs(entries, self.source)
        entries.pop("etc/ramdiag-runtime.sh")
        self.assertEqual(check.verify_initramfs(entries, self.source)["result"], "PASS")
        extra = cpio_entry("etc/ramdiag-unknown.sh", data=b"unknown")
        raw = self.fixture.cpio()
        # Add the unknown source-only helper before the trailer.
        trailer = cpio_entry("TRAILER!!!", 0)
        entries = parse_newc(raw[:-len(trailer)] + extra + trailer)
        (self.source / "initramfs/runtime.sh").write_bytes(self.fixture.source_files["runtime.sh"])
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            check.verify_initramfs(entries, self.source)

    def test_wrong_source_revision(self):
        (self.source / "REVISION").write_bytes(b"run3\n")
        with self.assertRaisesRegex(ValueError, "REVISION"):
            check.verify_artifacts(self.artifacts, self.source)

    def test_real_config_and_board_policy_fail_closed(self):
        path = self.artifacts / "kernel.config"
        original = path.read_bytes()
        path.write_bytes(original + b"CONFIG_BLOCK=y\n")
        with self.assertRaisesRegex(ValueError, "configuration failed"):
            check.verify_artifacts(self.artifacts, self.source)
        path.write_bytes(original)
        (self.artifacts / check.DTB_NAME).write_bytes(dtb_fixture())
        with self.assertRaises(ValueError):
            check.verify_artifacts(self.artifacts, self.source)

    def test_standalone_payload_mismatch_and_repack_are_checked(self):
        original = self.fixture.image
        (self.artifacts / "Image").write_bytes(original + b"different")
        with self.assertRaisesRegex(ValueError, "Extracted Image differs"):
            check.verify_artifacts(self.artifacts, self.source)
        (self.artifacts / "Image").write_bytes(original)
        with mock.patch.object(pack_boot, "pack", return_value=(b"wrong", b"wrong")):
            with self.assertRaisesRegex(ValueError, "repack differs"):
                check.verify_artifacts(self.artifacts, self.source)

    def test_consistent_but_wrong_diagnostic_cmdline_rejected(self):
        ramdisk = (self.artifacts / check.RAMDISK_NAME).read_bytes()
        boot, kernel = pack_boot.pack(self.fixture.image, self.fixture.dtb, ramdisk, "rdinit=/different")
        (self.artifacts / check.BOOT_NAME).write_bytes(boot)
        (self.artifacts / Path(check.BOOT_NAME).with_suffix(".json")).write_text(
            json.dumps(pack_boot.boot_manifest(boot, kernel, "rdinit=/different")))
        with self.assertRaisesRegex(ValueError, "diagnostic policy"):
            check.verify_artifacts(self.artifacts, self.source)

    def test_manifest_corruption_is_not_trusted(self):
        path = self.artifacts / Path(check.BOOT_NAME).with_suffix(".json")
        original = json.loads(path.read_text())
        for key, value in (("sha256", "0" * 64), ("size", 0), ("hardware_tested", True),
                           ("header_version", True), ("stage", "hardware tested")):
            path.write_text(json.dumps({**original, key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError):
                check.verify_artifacts(self.artifacts, self.source)

    def test_paths_symlinks_and_new_report_only(self):
        with self.assertRaises(ValueError):
            check.file_under(self.artifacts, "../source/REVISION")
        alias = self.artifacts / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            check.file_under(self.artifacts, "alias/REVISION")
        output = self.artifacts / "check.json"
        argv = ["--artifacts", str(self.artifacts), "--source-root", str(self.source),
                "--output", str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check.main(argv), 0)
        original = output.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main(argv), 1)
        self.assertEqual(output.read_bytes(), original)
        for path in (self.source / "scripts/new-code.json", self.source / "REVISION",
                     self.artifacts / "source-manifest.json", self.artifacts / "not-json.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                check.report_path(path, self.source, self.artifacts)

    def test_failed_check_never_writes_success_report(self):
        (self.artifacts / "Image").write_bytes(b"bad")
        output = self.artifacts / "check.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main(["--artifacts", str(self.artifacts),
                                         "--source-root", str(self.source), "--output", str(output)]), 1)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
