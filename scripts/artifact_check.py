#!/usr/bin/env python3
"""Read-only PCAM00 B-machine RAM-diagnostic artifact verification.

Run this script from a separately trusted checkout, not code obtained from the
artifact being checked::

    python3 scripts/artifact_check.py --artifacts DIR --source-root CHECKOUT \
        [--output NEW-REPORT.json]

DIR contains the fixed build.sh artifact names, including kernelrelease.txt.
CHECKOUT is the build source root (the directory containing scripts/, initramfs/,
config/, dts/, REVISION). Image must contain one IKCFG_ST/gzip/IKCFG_ED envelope
whose CRC-checked, bounded .config bytes equal kernel.config without rewriting.
Its one or two separate NUL-terminated Linux version strings (weak/final build
banners) must ALL match kernelrelease.txt
and the fixed B-v4 release suffix. These are strings/config evidence, not proof
that every instruction implements the claimed configuration.
No metadata controls an import, executable, extraction destination, or command.
No target program is executed. An optional source-manifest.json contains exactly
{"revision": "pcam00-b-ramdiag-v4", "files": {"relative/path":
{"size": 123, "sha256": "..."}}}; its recursive inventory must match CHECKOUT.
If DIR/sources exists, its same recursive source inventory must match too; the
copied code is only read and hashed, never imported or executed.
This establishes byte equality only, not an authenticated remote build/source
identity. Missing source manifests are explicitly reported as unverified.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

import image_formats
import pack_boot
import validate
from image_formats import (MAX_COMPRESSED, MAX_DTB, MAX_IMAGE,
                           gzip_member, parse_arm64_image, parse_fdt,
                           parse_ramdisk, parse_static_aarch64_elf,
                           read_regular, require, safe_relative_path)

# Policy constants deliberately do not come from downloaded metadata or a boot
# header. Keep them independent of the producer for meaningful negative checks.
PAGE = 4096
BOOT_LIMIT = 64 * 1024 * 1024
EXPECTED_CMDLINE = "rdinit=/init loglevel=7 ignore_loglevel printk.time=1 panic=15 clk_ignore_unused pd_ignore_unused"
REVISION = "pcam00-b-ramdiag-v4"
KERNEL_LOCALVERSION = "-oppo-" + REVISION
IKCONFIG_START, IKCONFIG_END = b"IKCFG_ST", b"IKCFG_ED"
IKCONFIG_LIMIT = 2 * 1024 * 1024
KERNEL_RELEASE_LIMIT = 64  # Linux UTS_RELEASE limit, excluding the final LF.
LINUX_VERSION_LIMIT = 1024  # Entire banner, excluding its terminating NUL.
REQUIRED_APPLETS = (
    "mount", "mkdir", "cat", "dmesg", "ip", "httpd", "sleep", "reboot",
    "ln", "chmod", "cp", "mv", "timeout", "setsid", "kill", "sh",
)
STAGE = "compile-only; not flashed or booted on Reno"
BOOT_NAME = "boot-pcam00-ramdiag-UNTESTED.img"
DTB_NAME = "sdm710-oppo-pcam00-ramdiag.dtb"
RAMDISK_NAME = "initramfs-debian-arm64.cpio.gz"
HELPER_MAP = {
    "safety.sh": "etc/ramdiag-safety.sh",
    "runtime.sh": "etc/ramdiag-runtime.sh",
    "usb.sh": "etc/ramdiag-usb.sh",
}
SOURCE_TREES = (".github", "config", "docs", "dts", "initramfs", "scripts")
SOURCE_ROOT_FILES = (".gitignore", "README.md", "REVISION")
SOURCE_LIMIT = 16 * 1024 * 1024
METADATA_LIMIT = 4 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_record(data):
    return {"size": len(data), "sha256": digest(data)}


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, f"Duplicate JSON key: {key}")
        obj[key] = value
    return obj


def load_json(path):
    try:
        result = json.loads(read_regular(path, METADATA_LIMIT).decode("utf-8"),
                            object_pairs_hook=_unique_object,
                            parse_constant=lambda value: (_ for _ in ()).throw(
                                ValueError(f"Invalid JSON constant: {value}")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Invalid JSON: {path.name}: {exc}") from exc
    require(isinstance(result, dict), f"JSON object required: {path.name}")
    return result


def file_under(root, relative):
    """Resolve a canonical regular file without symlinked ancestors."""
    root = root_directory(root)
    safe_relative_path(relative)
    candidate = root
    for index, part in enumerate(relative.split("/")):
        candidate = candidate / part
        info = candidate.lstat()
        require(not stat.S_ISLNK(info.st_mode), f"Symlinked input path: {relative}")
        if index < len(relative.split("/")) - 1:
            require(stat.S_ISDIR(info.st_mode), f"Non-directory input ancestor: {relative}")
        else:
            require(stat.S_ISREG(info.st_mode), f"Non-regular input: {relative}")
    require(candidate.resolve(strict=True).is_relative_to(root), f"Input outside root: {relative}")
    return candidate


def root_directory(path):
    path = Path(path)
    require(not path.is_symlink(), f"Symlinked input root: {path}")
    path = path.resolve(strict=True)
    require(path.is_dir(), f"Input root is not a directory: {path}")
    return path


@dataclass(frozen=True)
class BootImage:
    kernel: bytes
    ramdisk: bytes
    image: bytes
    dtb: bytes
    cpio: bytes
    entries: dict
    cmdline: str
    sha1: str


def parse_boot_v1(data):
    """Independently parse only the fixed v1 envelope, including every pad byte."""
    require(PAGE * 3 <= len(data) < BOOT_LIMIT and len(data) % PAGE == 0,
            "Boot image length/alignment out of bounds")
    require(data[:8] == b"ANDROID!", "Invalid boot magic")
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size,
     second_addr, tags_addr, page_size, version, os_version) = struct.unpack_from("<10I", data, 8)
    require((kernel_addr, ramdisk_addr, second_size, second_addr, tags_addr,
             page_size, version, os_version) == (0x8000, 0x1000000, 0, 0, 0x100, PAGE, 1, 0),
            "Unexpected boot geometry/version/second-stage fields")
    require(kernel_size > 0 and ramdisk_size > 0, "Empty boot payload")
    require(data[48:64] == b"pcam00-ramdiag".ljust(16, b"\0"), "Unexpected boot name")
    command_field = data[64:576]
    terminator = command_field.find(b"\0")
    require(0 <= terminator <= 511 and not any(command_field[terminator:]),
            "Unterminated boot cmdline or nonzero cmdline padding")
    command = command_field[:terminator]
    require(all(32 <= value <= 126 for value in command), "Nonprintable boot cmdline")
    require(not any(data[596:1632]), "Nonzero boot id padding/extra cmdline")
    require(struct.unpack_from("<IQI", data, 1632) == (0, 0, 1648),
            "Unexpected v1 DTBO size/offset/header size")
    require(not any(data[1648:PAGE]), "Nonzero boot header-page padding")
    kernel_end = PAGE + kernel_size
    ramdisk_start = PAGE + ((kernel_size + PAGE - 1) // PAGE) * PAGE
    ramdisk_end = ramdisk_start + ramdisk_size
    expected_end = ramdisk_start + ((ramdisk_size + PAGE - 1) // PAGE) * PAGE
    require(kernel_end <= ramdisk_start < ramdisk_end <= expected_end == len(data),
            "Truncated boot payload or trailing signature/DTBO/data")
    require(not any(data[kernel_end:ramdisk_start]) and not any(data[ramdisk_end:]),
            "Nonzero boot kernel/ramdisk padding")
    kernel = data[PAGE:kernel_end]
    ramdisk = data[ramdisk_start:ramdisk_end]
    # v1 legacy ID includes sizes for kernel, ramdisk, zero second and zero DTBO.
    ident = hashlib.sha1()
    ident.update(kernel)
    ident.update(struct.pack("<I", kernel_size))
    ident.update(ramdisk)
    ident.update(struct.pack("<I", ramdisk_size))
    ident.update(bytes(8))
    legacy_id = ident.digest()
    require(data[576:596] == legacy_id, "Boot SHA1 ID mismatch")
    require(kernel[:10] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff",
            "Unexpected kernel gzip metadata/encoding")
    image, gzip_size = gzip_member(kernel, MAX_IMAGE, allow_trailing=True)
    parse_arm64_image(image)
    dtb = kernel[gzip_size:]
    parse_fdt(dtb)  # Exact total size rejects second DTB, zero suffix or signature.
    cpio, entries = parse_ramdisk(ramdisk)
    return BootImage(kernel, ramdisk, image, dtb, cpio, entries,
                     command.decode("ascii"), legacy_id.hex())


def extract_ikconfig(image):
    """Extract the one Linux IKCONFIG envelope, without executing extract-ikconfig.

    Like scripts/extract-ikconfig, the payload begins immediately after IKCFG_ST;
    unlike a permissive gzip pipe, reject duplicate markers, trailing members,
    CRC/ISIZE errors and excessive output. Never strip or rewrite .config bytes.
    """
    parse_arm64_image(image)  # Bounds the searches even when called directly.
    require(image.count(IKCONFIG_START) == image.count(IKCONFIG_END) == 1,
            "Image must contain unique IKCFG_ST and IKCFG_ED markers")
    start, end = image.find(IKCONFIG_START), image.find(IKCONFIG_END)
    payload_start = start + len(IKCONFIG_START)
    require(start >= 64 and payload_start < end
            and end - payload_start <= METADATA_LIMIT, "Invalid IKCONFIG envelope bounds/order")
    config, _consumed = gzip_member(image[payload_start:end], IKCONFIG_LIMIT)
    return config, start, end


def config_values(data):
    """Recognize generated Linux .config text, not arbitrary gzip contents."""
    require(0 < len(data) <= IKCONFIG_LIMIT and data.endswith(b"\n"),
            "Embedded configuration must be bounded LF-terminated .config text")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Embedded configuration is not UTF-8 .config text") from exc
    require(all(c in "\n\t" or c.isprintable() for c in text),
            "Non-text/control byte in embedded configuration")
    lines = text.split("\n")
    require(lines[:2] == ["#", "# Automatically generated file; DO NOT EDIT."]
            and len(lines) >= 5 and lines[3] == "#"
            and re.fullmatch(r"# Linux/arm64 [^\s]+ Kernel Configuration", lines[2]),
            "Embedded configuration lacks the generated Linux/arm64 .config header")
    values = {}
    for line in lines:
        if not line:
            continue
        if line.startswith("#"):
            disabled = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
            if disabled is None:
                continue
            name, value = disabled[1], "n"
        else:
            setting = re.fullmatch(
                r'(CONFIG_[A-Za-z0-9_]+)=(y|m|n|-?[0-9]+|0x[0-9a-fA-F]+|"(?:[^"\\]|\\.)*")', line)
            require(setting is not None, "Malformed setting in embedded .config")
            name, value = setting.groups()
        require(name not in values, f"Duplicate embedded configuration symbol: {name}")
        values[name] = value
    require(values.get("CONFIG_IKCONFIG") == "y", "CONFIG_IKCONFIG must be y in Image")
    require(values.get("CONFIG_LOCALVERSION") == '"' + KERNEL_LOCALVERSION + '"',
            "Embedded CONFIG_LOCALVERSION must identify the fixed B-v4 release")
    return values


def kernel_release(data):
    """Accept one short make-kernelrelease line, with a verifier-owned suffix."""
    require(1 < len(data) <= KERNEL_RELEASE_LIMIT + 1,
            "kernelrelease.txt line length out of bounds")
    # No strip(): whitespace, extra lines, old revisions, suffix extensions and
    # metadata-selected policies must fail. A bare '+' is normal setlocalversion
    # output for an untagged commit even with CONFIG_LOCALVERSION_AUTO disabled.
    pattern = (rb"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-rc[0-9]+)?"
               + re.escape(KERNEL_LOCALVERSION.encode("ascii")) + rb"\+?\n")
    require(re.fullmatch(pattern, data) is not None,
            "kernelrelease.txt must be one LF-terminated B-v4 release with the fixed suffix")
    return data[:-1].decode("ascii")


def kernel_banners(image, release, config_start, config_end):
    """Check every banner, including the weak and final timestamp definitions.

    Pinned init/version.c compiles weak linux_banner first; version-timestamp.c
    supplies the final definition later. A real Image can retain both strings.
    Permit at most those two; every occurrence must independently match release.
    This does not identify which symbol/address the runtime actually prints.
    """
    marker = b"Linux version "
    offsets = [m.start() for m in re.finditer(re.escape(marker), image)]
    require(1 <= len(offsets) <= 2, "Image must contain one or two Linux version strings")
    banners = []
    prefix = marker + release.encode("ascii") + b" "
    for banner_start in offsets:
        require(banner_start >= 64 and not 32 <= image[banner_start - 1] <= 126,
                "Linux version must start a separate printable string")
        banner_end = image.find(b"\0", banner_start, banner_start + LINUX_VERSION_LIMIT + 1)
        require(banner_end > banner_start, "Linux version string is unterminated or too long")
        require(banner_end < config_start or banner_start >= config_end + len(IKCONFIG_END),
                "Linux version string must be outside IKCONFIG (not gzip metadata)")
        banner = image[banner_start:banner_end]
        require(banner.startswith(prefix), "Image Linux version does not match kernelrelease.txt")
        line = banner[:-1] if banner.endswith(b"\n") else banner
        require(len(line) > len(prefix) and all(32 <= c <= 126 for c in line),
                "Invalid Linux version line/build description")
        banners.append({"offset": banner_start, "text": banner.decode("ascii"),
                        "nul_terminated": True})
    return {**banners[0], "text_represents_first_match": True,
            "occurrence_count": len(banners), "all_match_release": True,
            "banners": banners, "runtime_canonical_symbol_identified": False,
            "note": "Up to two weak/final build banners; all checked, not arbitrarily selecting a matching one"}


def verify_kernel_image(image, config_bytes, release_bytes):
    config, start, end = extract_ikconfig(image)
    values = config_values(config)
    require(config == config_bytes, "Embedded IKCONFIG differs byte-for-byte from kernel.config")
    release = kernel_release(release_bytes)
    banners = kernel_banners(image, release, start, end)
    return {"result": "PASS", "embedded_config_byte_equal": True,
            "embedded_config": {**file_record(config), "format": "generated Linux/arm64 .config",
                                "start_marker_offset": start, "end_marker_offset": end,
                                "gzip_size": end - start - len(IKCONFIG_START),
                                "gzip_crc_isize_checked": True, "max_output": IKCONFIG_LIMIT,
                                "CONFIG_IKCONFIG": values["CONFIG_IKCONFIG"]},
            "kernelrelease": release, "release_matches_image": True,
            "linux_version": banners,
            "complete_code_verification": False,
            "warning": "Valid embedded gzip/config and Linux version string evidence only; "
                       "not formal verification of the complete kernel code"}


def source_inventory(root):
    """Recursive reviewed-source scope, excluding bytecode and generated outputs.

    Artifact directories/build.log may live inside the checkout; they are not
    source. The scope is fixed in code, never supplied by artifact metadata.
    """
    root = root_directory(root)
    result, total_size = {}, 0
    for tree in SOURCE_TREES:
        directory = root / tree
        if not os.path.lexists(directory):
            continue
        require(directory.is_dir() and not directory.is_symlink(), f"Invalid source tree: {tree}")
        def walk_error(error):
            raise error

        for current, dirs, files in os.walk(directory, followlinks=False, onerror=walk_error):
            require(len(Path(current).relative_to(root).parts) < 128, "Source tree nesting too deep")
            dirs[:] = sorted(name for name in dirs if name not in (".git", "__pycache__"))
            for name in dirs:
                require(not (Path(current) / name).is_symlink(), "Symlink in source tree")
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                relative = (Path(current) / name).relative_to(root).as_posix()
                result[relative] = file_record(read_regular(file_under(root, relative), SOURCE_LIMIT))
                total_size += result[relative]["size"]
                require(len(result) <= 32768 and total_size <= 256 * 1024 * 1024,
                        "Source inventory count/size limit exceeded")
    for relative in SOURCE_ROOT_FILES:
        if os.path.lexists(root / relative):
            result[relative] = file_record(read_regular(file_under(root, relative), SOURCE_LIMIT))
    return dict(sorted(result.items()))


def verify_source_manifest(artifacts, source_root):
    path = artifacts / "source-manifest.json"
    if not os.path.lexists(path):
        return {"result": "NOT_VERIFIED", "manifest_present": False,
                "downloaded_sources": {"present": os.path.lexists(artifacts / "sources"),
                                       "result": "NOT_VERIFIED"},
                "source_identity_authenticated": False,
                "warning": "Supplied source presence is not downloaded-source identity evidence"}
    manifest = load_json(file_under(artifacts, "source-manifest.json"))
    require(set(manifest) == {"revision", "files"}, "Unexpected source-manifest fields")
    require(manifest.get("revision") == REVISION, "Source-manifest revision mismatch")
    files = manifest.get("files")
    require(isinstance(files, dict) and 0 < len(files) <= 32768, "Invalid source-manifest files map")
    for name, expected in files.items():
        safe_relative_path(name)
        require(isinstance(expected, dict) and set(expected) == {"size", "sha256"}
                and type(expected.get("size")) is int
                and 0 <= expected["size"] <= SOURCE_LIMIT
                and isinstance(expected.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]),
                f"Invalid source-manifest record: {name}")
    actual = source_inventory(source_root)
    require(set(files) == set(actual),
            "Source-manifest recursive inventory mismatch (missing, extra, or unsupported source files)")
    for name, record in actual.items():
        require(files[name]["size"] == record["size"] and files[name]["sha256"] == record["sha256"],
                f"Source-manifest hash/size mismatch: {name}")
    downloaded_sources = {"present": False, "result": "NOT_PRESENT"}
    sources_dir = artifacts / "sources"
    if os.path.lexists(sources_dir):
        copied = source_inventory(root_directory(sources_dir))
        require(copied == actual, "Downloaded sources inventory/hash mismatch against supplied source")
        downloaded_sources = {"present": True, "result": "PASS", "files_checked": len(copied),
                              "byte_hashes_match_supplied_source": True}
    # The source corpus must describe the verifier we actually ran. Do NOT load
    # or replace modules from --source-root, artifact/sources, or manifest paths.
    trusted_modules = {"artifact_check.py": Path(__file__),
                       "image_formats.py": Path(image_formats.__file__),
                       "pack_boot.py": Path(pack_boot.__file__),
                       "validate.py": Path(validate.__file__)}
    for name, loaded in trusted_modules.items():
        record = actual.get("scripts/" + name)
        require(record == file_record(read_regular(loaded, SOURCE_LIMIT)),
                f"Supplied source does not match running trusted verifier: scripts/{name}")
    return {"result": "PASS", "manifest_present": True, "files_checked": len(actual),
            "manifest_sha256": digest(read_regular(path, METADATA_LIMIT)),
            "recursive_hashes_match_supplied_source": True,
            "downloaded_sources": downloaded_sources,
            "running_verifier_matches_supplied_source": True,
            "source_identity_authenticated": False,
            "warning": "Hash equality is not remote-source authentication or proof of a reproducible kernel build"}


def verify_initramfs(entries, source_root):
    revision_bytes = read_regular(file_under(source_root, "REVISION"), 128)
    require(revision_bytes == (REVISION + "\n").encode("ascii"), "Wrong source REVISION marker")

    def regular(name, *, mode=None):
        entry = entries.get(name)
        require(entry is not None and stat.S_ISREG(entry.mode), f"Required regular cpio file missing: {name}")
        if mode is not None:
            require(stat.S_IMODE(entry.mode) == mode, f"Wrong cpio mode: {name} (need {mode:04o})")
        return entry.data

    checked = {}
    source_init = read_regular(file_under(source_root, "initramfs/init"), SOURCE_LIMIT)
    require(source_init.startswith(b"#!/bin/sh\n"), "Source init has unexpected interpreter")
    require(regular("init", mode=0o755) == source_init, "cpio /init differs from supplied source")
    checked["init"] = file_record(source_init)
    expected_helpers = set()
    helper_sources = {path.name for path in (source_root / "initramfs").glob("*.sh")}
    require(helper_sources <= set(HELPER_MAP) | {"build.sh"},
            "Unmapped initramfs shell helper source; update the explicit HELPER_MAP")
    for source_name, target_name in HELPER_MAP.items():
        candidate = source_root / "initramfs" / source_name
        present = os.path.lexists(candidate)
        require(source_name != "safety.sh" or present, "Source safety.sh is required")
        if not present:
            require(target_name not in entries, f"cpio helper lacks supplied source counterpart: {target_name}")
            continue
        body = read_regular(file_under(source_root, "initramfs/" + source_name), SOURCE_LIMIT)
        require(regular(target_name) == body, f"cpio helper differs from supplied source: {target_name}")
        require(stat.S_IMODE(entries[target_name].mode) in (0o644, 0o755),
                f"Unsafe cpio helper permissions: {target_name}")
        expected_helpers.add(target_name)
        checked[target_name] = file_record(body)
    actual_helpers = {name for name in entries if name.startswith("etc/ramdiag-")}
    require(actual_helpers == expected_helpers | {"etc/ramdiag-revision"},
            "Unmapped/missing ramdiag helper or revision file")
    require(regular("etc/ramdiag-revision", mode=0o644) == revision_bytes,
            "cpio revision differs from source REVISION")
    require(regular("etc/recovery-seconds", mode=0o644) == b"180\n", "Recovery timer must be exactly 180 seconds")
    for name, major, minor, mode in (("dev/console", 5, 1, 0o600), ("dev/null", 1, 3, 0o666)):
        entry = entries.get(name)
        require(entry is not None and stat.S_ISCHR(entry.mode)
                and (entry.rdevmajor, entry.rdevminor, stat.S_IMODE(entry.mode)) == (major, minor, mode),
                f"Invalid/missing required character device: {name}")
    char_nodes = {name for name, entry in entries.items() if stat.S_ISCHR(entry.mode)}
    require(char_nodes == {"dev/console", "dev/null"}, "Unexpected cpio character device")
    busybox = regular("bin/busybox", mode=0o755)
    elf = parse_static_aarch64_elf(busybox)
    # Fixed policy matches the reviewed initramfs/build.sh applet set. Do not
    # source that script or trust an artifact-provided list. Each alias must be
    # the direct relative symlink to the already-checked bin/busybox ELF, not a
    # chain, wrapper, hardlink, absolute path, or a different executable. This
    # checks archive wiring only, not BusyBox's runtime applet implementations.
    aliases = {}
    for applet in REQUIRED_APPLETS:
        name = "bin/" + applet
        entry = entries.get(name)
        require(entry is not None and stat.S_ISLNK(entry.mode) and entry.data == b"busybox",
                f"cpio /{name} must link relatively and directly to busybox")
        aliases[name] = "busybox"
    checked["etc/ramdiag-revision"] = file_record(revision_bytes)
    return {"result": "PASS", "entries": len(entries), "revision": REVISION,
            "root_owned": True, "block_nodes": 0, "safe_relative_symlinks": True,
            "recovery_seconds": 180, "source_byte_comparisons": checked,
            "busybox": {**elf, "sha256": digest(busybox)},
            "required_applet_symlinks": {"result": "PASS", "aliases": aliases,
                                        "target": "bin/busybox", "target_elf_verified": True,
                                        "runtime_applets_verified": False},
            "init_executed": False}


def verify_boot_manifest(artifacts, boot, parsed):
    manifest = load_json(file_under(artifacts, Path(BOOT_NAME).with_suffix(".json").name))
    expected = {"status": "COMPILE_ONLY_UNTESTED_DO_NOT_FLASH",
                "header_version": 1, "page_size": PAGE,
                "kernel_addr": "0x00008000", "ramdisk_addr": "0x01000000", "tags_addr": "0x00000100",
                "kernel_encoding": "gzip ARM64 Image + single Reno DTB",
                "cmdline": EXPECTED_CMDLINE, "size": len(boot),
                "kernel_field_size": len(parsed.kernel), "sha256": digest(boot)}
    for key, value in expected.items():
        require(type(manifest.get(key)) is type(value) and manifest[key] == value,
                f"Boot manifest mismatch: {key}")
    if "hardware_tested" in manifest:
        require(manifest["hardware_tested"] is False, "Boot manifest must not claim hardware testing")
    if "stage" in manifest:
        require(manifest["stage"] == STAGE, "Boot manifest stage mismatch")
    return {"result": "PASS", "sha256": digest(boot)}


def verify_artifacts(artifacts, source_root):
    artifacts = root_directory(artifacts)
    source_root = root_directory(source_root)
    require(artifacts != source_root, "Artifact and source roots must be distinct")
    source_report = verify_source_manifest(artifacts, source_root)
    inputs = {
        "Image": read_regular(file_under(artifacts, "Image"), MAX_IMAGE),
        DTB_NAME: read_regular(file_under(artifacts, DTB_NAME), MAX_DTB),
        RAMDISK_NAME: read_regular(file_under(artifacts, RAMDISK_NAME), MAX_COMPRESSED),
        BOOT_NAME: read_regular(file_under(artifacts, BOOT_NAME), BOOT_LIMIT - 1),
    }
    config_path = file_under(artifacts, "kernel.config")
    config_bytes = read_regular(config_path, IKCONFIG_LIMIT)
    release_bytes = read_regular(file_under(artifacts, "kernelrelease.txt"), KERNEL_RELEASE_LIMIT + 1)
    try:
        config_result = validate.require_config(config_path)
    except SystemExit as exc:
        raise ValueError(f"Kernel configuration failed: {exc}") from exc
    require(config_bytes == read_regular(config_path, IKCONFIG_LIMIT), "Configuration changed during validation")
    # Structural FDT parsing is always strict even if validate.py is an older
    # trusted board-policy implementation; require_dtb supplies board invariants.
    parse_fdt(inputs[DTB_NAME])
    dtb_path = file_under(artifacts, DTB_NAME)
    try:
        dtb_result = validate.require_dtb(dtb_path)
    except (KeyError, IndexError, struct.error, SystemExit) as exc:
        raise ValueError(f"Board policy validation failed: {exc}") from exc
    require(inputs[DTB_NAME] == read_regular(dtb_path, MAX_DTB), "DTB changed during validation")
    parsed = parse_boot_v1(inputs[BOOT_NAME])
    require(parsed.cmdline == EXPECTED_CMDLINE, "Boot cmdline does not match diagnostic policy")
    require(parsed.image == inputs["Image"], "Extracted Image differs from standalone artifact")
    require(parsed.dtb == inputs[DTB_NAME], "Extracted DTB differs from standalone artifact")
    require(parsed.ramdisk == inputs[RAMDISK_NAME], "Extracted ramdisk differs from standalone artifact")
    image_report = verify_kernel_image(inputs["Image"], config_bytes, release_bytes)
    plain, _entries = parse_ramdisk(inputs[RAMDISK_NAME])
    require(parsed.cpio == plain, "Extracted cpio differs from standalone archive")
    ramdisk_report = verify_initramfs(parsed.entries, source_root)
    # Separate producer roundtrip in memory, only after independent header,
    # bounds, padding, digest, gzip and cpio checks above have passed.
    repacked, kernel = pack_boot.pack(inputs["Image"], inputs[DTB_NAME], inputs[RAMDISK_NAME])
    require(repacked == inputs[BOOT_NAME] and kernel == parsed.kernel,
            "Independent in-memory repack differs from downloaded boot image")
    manifest_report = verify_boot_manifest(artifacts, inputs[BOOT_NAME], parsed)
    return {
        "result": "PASS", "stage": STAGE, "revision": REVISION,
        "hardware_tested": False, "device_operations_performed": False,
        "target_code_executed": False,
        "warning": "Structural/byte checks only; no boot, USB, thermal, battery or bootloader validation; DO NOT FLASH",
        "config": config_result, "kernel_image": image_report, "device_tree": dtb_result,
        "source_manifest": source_report, "initramfs": ramdisk_report,
        "boot": {"result": "PASS", "header_version": 1, "page_size": PAGE,
                 "kernel_addr": "0x00008000", "ramdisk_addr": "0x01000000", "tags_addr": "0x00000100",
                 "cmdline": parsed.cmdline, "legacy_sha1": parsed.sha1,
                 "sha256": digest(inputs[BOOT_NAME]), "zero_padding": True,
                 "single_dtb": True, "signature_present": False, "dtbo_present": False,
                 "extracted_inputs_byte_equal": True, "in_memory_repack_byte_equal": True},
        "boot_manifest": manifest_report,
        "files": {**{name: file_record(data) for name, data in inputs.items()},
                  "kernel.config": file_record(config_bytes),
                  "kernelrelease.txt": file_record(release_bytes)},
    }


def report_path(path, source_root, artifacts):
    source_root = root_directory(source_root)
    artifacts = root_directory(artifacts)
    path = Path(path)
    destination = path.parent.resolve(strict=True) / path.name
    require(not os.path.lexists(destination), f"Refusing existing report output: {destination}")
    # A caller typo must not create/overwrite a trusted Python module, source
    # file or an artifact used as input. Only a new JSON report is writable.
    require(destination.suffix == ".json", "Report output must use a .json suffix")
    protected = (Path(__file__).resolve().parent,)
    require(not any(destination.is_relative_to(root) for root in protected),
            "Report output cannot be written into verifier code directory")
    if destination.is_relative_to(source_root):
        relative = destination.relative_to(source_root)
        require(relative.parts[0] not in SOURCE_TREES and relative.as_posix() not in SOURCE_ROOT_FILES,
                "Report output cannot be written into reviewed source scope")
    if destination.is_relative_to(artifacts):
        relative = destination.relative_to(artifacts)
        require(relative.parts[0] != "sources", "Report output cannot modify downloaded source copies")
    if destination.parent == artifacts:
        require(destination.name not in ("manifest.json", "source-manifest.json",
                                          Path(BOOT_NAME).with_suffix(".json").name),
                "Report output collides with artifact metadata")
    return destination


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--output", type=Path, help="new .json report only (never overwrite)")
    args = ap.parse_args(argv)
    try:
        artifacts = root_directory(args.artifacts)
        source_root = root_directory(args.source_root)
        destination = report_path(args.output, source_root, artifacts) if args.output else None
        report = verify_artifacts(artifacts, source_root)
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if destination:
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(text)
    except (ValueError, OSError, UnicodeError, struct.error) as exc:
        print(f"artifact_check: FAIL: {exc}", file=sys.stderr)
        return 1
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
