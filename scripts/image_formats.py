#!/usr/bin/env python3
"""Bounded, read-only binary parsers for the RAM-diagnostic build tools.

These functions never extract archive members to disk or execute target code.
They deliberately accept only the formats emitted by this build, not arbitrary
boot images, FDT versions, compressed archives, or cpio extensions.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import struct
import zlib

MAX_IMAGE = 256 * 1024 * 1024
MAX_DTB = 4 * 1024 * 1024
MAX_CPIO = 64 * 1024 * 1024
MAX_COMPRESSED = 64 * 1024 * 1024
MAX_ENTRIES = 32768
MAX_PATH = 4096


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_regular(path, limit):
    """Read a bounded regular file without following a final-component symlink."""
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit,
                f"Not a bounded regular file: {path}")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        require(len(data) == before.st_size and len(data) <= limit
                and (before.st_size, before.st_mtime_ns, before.st_ino)
                == (after.st_size, after.st_mtime_ns, after.st_ino),
                f"File changed during read: {path}")
    return data


def safe_relative_path(name):
    """Accept a canonical relative POSIX path, never an extraction directive."""
    require(isinstance(name, str) and 0 < len(name.encode("utf-8")) <= MAX_PATH,
            "Invalid relative path length/type")
    require(name.isprintable() and "\\" not in name and ":" not in name,
            f"Unsafe relative path: {name!r}")
    require(name.count("/") < 128 and all(p not in ("", ".", "..") for p in name.split("/")),
            f"Noncanonical, escaping or overly deep path: {name!r}")
    return name


def parse_arm64_image(data):
    require(64 <= len(data) <= MAX_IMAGE, "ARM64 Image length out of bounds")
    require(data[56:60] == b"ARM\x64", "Not an ARM64 Linux Image")
    offset, image_size, flags = struct.unpack_from("<QQQ", data, 8)
    require(not flags & 1, "Big-endian ARM64 Image is unsupported")
    # image_size includes the in-memory tail, so it need not equal file length.
    require(image_size <= 2 * MAX_IMAGE, "ARM64 Image memory size out of bounds")
    return {"file_size": len(data), "text_offset": offset,
            "memory_size": image_size, "flags": flags}


def gzip_member(data, max_output, *, allow_trailing=False):
    """Inflate exactly one CRC/ISIZE-checked member, with an output size limit.

    Return (plain_bytes, consumed_compressed_bytes). Only the kernel field may
    have a trailing object (its single FDT); cpio gzip concatenation is rejected.
    """
    require(18 <= len(data) <= MAX_COMPRESSED, "gzip input length out of bounds")
    require(data[:3] == b"\x1f\x8b\x08" and not data[3] & 0xe0,
            "Invalid gzip header")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        plain = decoder.decompress(data, max_output + 1)
    except zlib.error as exc:
        raise ValueError(f"Invalid gzip member/CRC: {exc}") from exc
    require(len(plain) <= max_output and not decoder.unconsumed_tail,
            "gzip decompression limit exceeded")
    require(decoder.eof, "Truncated gzip member")
    used = len(data) - len(decoder.unused_data)
    require(allow_trailing or used == len(data), "Trailing bytes or concatenated gzip members")
    return plain, used


def parse_fdt(data):
    """Parse one v17 FDT, checking every block, token, padding and terminator."""
    require(56 <= len(data) <= MAX_DTB, "FDT length out of bounds")
    (magic, total, off_struct, off_strings, off_reserve, version, compatible,
     _boot_cpu, strings_size, struct_size) = struct.unpack_from(">10I", data)
    require(magic == 0xd00dfeed and total == len(data), "Invalid FDT magic/total size")
    require(version == 17 and compatible in (16, 17), "Unsupported FDT version")
    require(off_struct >= 40 and off_struct % 4 == 0 and struct_size >= 16
            and struct_size % 4 == 0 and off_struct + struct_size <= total,
            "FDT structure block out of bounds/alignment")
    require(off_strings >= 40 and off_strings + strings_size <= total,
            "FDT strings block out of bounds")
    require(off_reserve >= 40 and off_reserve % 8 == 0 and off_reserve + 16 <= total,
            "FDT reservation map out of bounds/alignment")
    blocks = [(0, 40), (off_struct, off_struct + struct_size)]
    if strings_size:
        blocks.append((off_strings, off_strings + strings_size))
    # A reservation list must terminate before entering any other block.
    reserve_limit = min([start for start, _ in blocks if start >= off_reserve] + [total])
    pos = off_reserve
    while True:
        require(pos + 16 <= reserve_limit, "Unterminated or overlapping FDT reservation map")
        address, size = struct.unpack_from(">QQ", data, pos)
        pos += 16
        if address == size == 0:
            break
        require(size > 0 and address + size <= 1 << 64, "Invalid FDT reservation range")
    blocks.append((off_reserve, pos))
    last = 0
    for start, end in sorted(blocks):
        require(start >= last, "Overlapping FDT blocks")
        require(not any(data[last:start]), "Nonzero FDT block gap/padding")
        last = end
    require(not any(data[last:]), "Unexpected FDT trailer")
    strings = data[off_strings:off_strings + strings_size]
    require(not strings or strings[-1] == 0, "Unterminated FDT strings block")
    require(all(c == 0 or 33 <= c <= 126 for c in strings), "Invalid FDT string bytes")
    pos, end = off_struct, off_struct + struct_size
    nodes, stack, children_seen = {}, [], []
    root_seen = root_closed = ended = False
    token_count = path_bytes = 0
    while pos < end:
        require(pos + 4 <= end, "Truncated FDT token")
        tag, = struct.unpack_from(">I", data, pos)
        pos += 4
        token_count += 1
        require(token_count <= 262144, "Too many FDT tokens")
        if tag == 1:  # FDT_BEGIN_NODE
            require(not root_closed and len(stack) < 128, "Invalid FDT root/nesting")
            stop = data.find(b"\0", pos, end)
            require(stop >= 0, "Unterminated FDT node name")
            raw_name = data[pos:stop]
            require(len(raw_name) <= 256, "FDT node name too long")
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError("Non-ASCII FDT node name") from exc
            if not stack:
                require(not root_seen and name == "", "FDT must have one empty-name root")
                root_seen = True
                path = "/"
            else:
                require(bool(re.fullmatch(r"[A-Za-z0-9,._+@-]+", name))
                        and name not in (".", ".."), "Invalid FDT node name")
                children_seen[-1] = True
                path = stack[-1].rstrip("/") + "/" + name
            aligned = (stop + 4) & ~3
            require(aligned <= end and not any(data[stop + 1:aligned]),
                    "Invalid FDT node-name padding")
            pos = aligned
            require(path not in nodes, f"Duplicate FDT node: {path}")
            path_bytes += len(path)
            require(len(path) <= MAX_PATH and len(nodes) < MAX_ENTRIES
                    and path_bytes <= 16 * 1024 * 1024, "FDT node/path limits exceeded")
            nodes[path] = {}
            stack.append(path)
            children_seen.append(False)
        elif tag == 2:  # FDT_END_NODE
            require(bool(stack), "FDT node stack underflow")
            stack.pop()
            children_seen.pop()
            if not stack:
                root_closed = True
        elif tag == 3:  # FDT_PROP
            require(bool(stack) and not children_seen[-1], "FDT property outside node/after child")
            require(pos + 8 <= end, "Truncated FDT property header")
            size, name_offset = struct.unpack_from(">II", data, pos)
            pos += 8
            require(name_offset < len(strings), "FDT property name offset out of bounds")
            stop = strings.find(b"\0", name_offset)
            require(stop > name_offset, "Invalid FDT property name")
            require(0 <= stop - name_offset <= 256, "FDT property name too long")
            name = strings[name_offset:stop].decode("ascii")
            require(bool(re.fullmatch(r"[A-Za-z0-9,._+?#-]+", name)), "Invalid FDT property name")
            aligned = (pos + size + 3) & ~3
            require(pos + size <= end and aligned <= end, "Truncated FDT property payload")
            require(not any(data[pos + size:aligned]), "Nonzero FDT property padding")
            require(name not in nodes[stack[-1]], f"Duplicate FDT property: {name}")
            nodes[stack[-1]][name] = data[pos:pos + size]
            pos = aligned
        elif tag == 4:  # FDT_NOP
            continue
        elif tag == 9:  # FDT_END
            require(root_seen and root_closed and not stack, "Premature FDT_END")
            require(pos == end, "FDT structure data after FDT_END")
            ended = True
            break
        else:
            raise ValueError(f"Unexpected FDT token: {tag}")
    require(ended, "Missing FDT_END")
    return nodes


@dataclass(frozen=True)
class CpioEntry:
    name: str
    mode: int
    uid: int
    gid: int
    nlink: int
    mtime: int
    data: bytes
    rdevmajor: int
    rdevminor: int


def _link_components(data):
    require(0 < len(data) <= MAX_PATH, "Symlink target length out of bounds")
    try:
        target = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid symlink target encoding") from exc
    require(target.isprintable() and "\\" not in target and ":" not in target
            and not target.startswith("/") and target.count("/") < 128
            and all(target.split("/")),
            "Absolute or unsafe symlink target")
    return target.split("/")


def _check_archive_paths(entries):
    for name, entry in entries.items():
        parts = name.split("/")
        for count in range(1, len(parts)):
            parent = entries.get("/".join(parts[:count]))
            require(parent is not None and stat.S_ISDIR(parent.mode),
                    f"Missing/non-directory cpio ancestor: {name}")
        if stat.S_ISLNK(entry.mode):
            components = _link_components(entry.data)
            depth = len(name.split("/")) - 1
            for component in components:
                if component == "..":
                    require(depth > 0, f"Escaping symlink: {name}")
                    depth -= 1
                elif component != ".":
                    depth += 1
        if not stat.S_ISLNK(entry.mode):
            continue
        # Resolve all components in the virtual archive, not on the host. This
        # catches escapes through chained links as well as cycles and dangling
        # targets. Archive members themselves may never be under symlinks.
        pending = parts[:-1] + _link_components(entry.data)
        resolved, hops = [], 0
        while pending:
            component = pending.pop(0)
            if component == ".":
                continue
            if component == "..":
                require(bool(resolved), f"Escaping symlink: {name}")
                resolved.pop()
                continue
            candidate = "/".join(resolved + [component])
            target = entries.get(candidate)
            require(target is not None, f"Dangling cpio symlink: {name}")
            if stat.S_ISLNK(target.mode):
                hops += 1
                require(hops <= 40, f"Cyclic/deep cpio symlink: {name}")
                pending = _link_components(target.data) + pending
                require(len(pending) + len(resolved) <= 256, "Expanded symlink path too deep")
            else:
                require(not pending or stat.S_ISDIR(target.mode),
                        f"Non-directory symlink component: {name}")
                resolved.append(component)


def parse_newc(data):
    """Parse a single root-owned newc archive; never materialize its paths."""
    require(110 <= len(data) <= MAX_CPIO, "newc archive length out of bounds")
    pos, entries, trailer = 0, {}, False
    while pos < len(data):
        require(pos + 110 <= len(data), "Truncated newc header")
        header = data[pos:pos + 110]
        require(header[:6] == b"070701" and re.fullmatch(b"[0-9a-fA-F]{104}", header[6:]),
                "Invalid newc magic/hex header")
        fields = [int(header[n:n + 8], 16) for n in range(6, 110, 8)]
        (_ino, mode, uid, gid, nlink, mtime, size, devmajor, devminor,
         rdevmajor, rdevminor, namesize, check) = fields
        require(uid == gid == check == 0, "newc entries must be root-owned with zero check field")
        require(1 <= namesize <= MAX_PATH + 1, "newc name length out of bounds")
        name_start = pos + 110
        name_end = name_start + namesize
        body_start = (name_end + 3) & ~3
        body_end = body_start + size
        next_pos = (body_end + 3) & ~3
        require(next_pos <= len(data), "Truncated newc name/data")
        raw_name = data[name_start:name_end]
        require(raw_name[-1:] == b"\0" and b"\0" not in raw_name[:-1],
                "Invalid newc name terminator")
        require(not any(data[name_end:body_start]) and not any(data[body_end:next_pos]),
                "Nonzero newc name/data padding")
        try:
            name = raw_name[:-1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid newc path encoding") from exc
        body = data[body_start:body_end]
        pos = next_pos
        if name == "TRAILER!!!":
            require(mode == size == devmajor == devminor == rdevmajor == rdevminor == 0
                    and nlink == 1, "Invalid newc trailer fields")
            require(len(data) - pos < 512 and len(data) % 4 == 0 and not any(data[pos:]),
                    "Unexpected newc trailer/concatenated archive")
            trailer = True
            break
        safe_relative_path(name)
        require(name not in entries, f"Duplicate newc path: {name}")
        parent_name, separator, _leaf = name.rpartition("/")
        if separator:
            parent = entries.get(parent_name)
            require(parent is not None and stat.S_ISDIR(parent.mode),
                    f"newc parent must precede child as a directory: {name}")
        require(len(entries) < MAX_ENTRIES, "Too many newc entries")
        kind = stat.S_IFMT(mode)
        require(kind in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK, stat.S_IFCHR),
                f"Unsupported/block newc node: {name}")
        require(not mode & ~0o177777 and not mode & 0o6000,
                f"Unexpected or set-id newc permissions: {name}")
        require(nlink == 1 or (kind == stat.S_IFDIR and nlink >= 2),
                f"Unsupported newc hardlink/link count: {name}")
        require(kind in (stat.S_IFREG, stat.S_IFLNK) or size == 0,
                f"Unexpected newc node payload: {name}")
        require(kind == stat.S_IFCHR or rdevmajor == rdevminor == 0,
                f"Unexpected newc device numbers: {name}")
        if kind == stat.S_IFLNK:
            _link_components(body)
        entries[name] = CpioEntry(name, mode, uid, gid, nlink, mtime, body, rdevmajor, rdevminor)
    require(trailer and entries, "Missing newc trailer or empty archive")
    _check_archive_paths(entries)
    return entries


def parse_ramdisk(data):
    plain, _ = gzip_member(data, MAX_CPIO)
    return plain, parse_newc(plain)


def parse_static_aarch64_elf(data):
    """Check ELF64 AArch64/static structure without executing BusyBox."""
    require(64 <= len(data) <= MAX_CPIO, "BusyBox ELF length out of bounds")
    require(data[:7] == b"\x7fELF\x02\x01\x01" and data[7] in (0, 3)
            and not any(data[9:16]), "BusyBox must be little-endian ELF64")
    (elf_type, machine, version, entry, phoff, shoff, _flags, ehsize,
     phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    require(elf_type in (2, 3) and machine == 183 and version == 1 and ehsize == 64,
            "BusyBox is not an AArch64 executable")
    require(phentsize == 56 and 0 < phnum <= 1024 and phoff >= 64
            and phoff + phnum * phentsize <= len(data), "Invalid ELF program-header bounds")
    if shnum:
        require(shentsize == 64 and shoff >= 64 and shoff + shnum * shentsize <= len(data)
                and shstrndx < shnum, "Invalid ELF section-header bounds")
    else:
        require(shoff == shstrndx == 0, "Unsupported extended ELF section numbering")
    executable_entry = False
    load_count = 0
    for index in range(phnum):
        kind, flags, offset, vaddr, _paddr, filesz, memsz, align = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + index * phentsize)
        require(kind not in (3, 5), "BusyBox has PT_INTERP or PT_SHLIB (not static)")
        require(offset + filesz <= len(data), "ELF segment out of bounds")
        require(align in (0, 1) or align & (align - 1) == 0, "Invalid ELF segment alignment")
        if kind == 1:  # PT_LOAD
            require(filesz <= memsz and vaddr + memsz < 1 << 64 and not flags & ~7,
                    "Invalid ELF load segment")
            require(align in (0, 1) or offset % align == vaddr % align,
                    "Misaligned ELF load segment")
            load_count += 1
            executable_entry |= bool(flags & 1 and vaddr <= entry < vaddr + filesz)
        if kind == 2:  # PT_DYNAMIC: static PIE is allowed, dependencies are not.
            require(filesz % 16 == 0, "Truncated ELF dynamic table")
            terminated = False
            for at in range(offset, offset + filesz, 16):
                tag, _value = struct.unpack_from("<qQ", data, at)
                require(tag != 1, "BusyBox has DT_NEEDED (not static)")
                if tag == 0:
                    require(not any(data[at:offset + filesz]), "Nonzero ELF dynamic-table trailer")
                    terminated = True
                    break
            require(terminated, "Unterminated ELF dynamic table")
    require(load_count and executable_entry, "ELF entry is not in a file-backed executable segment")
    return {"class": "ELF64", "machine": "AArch64", "pt_interp": False,
            "dynamic_dependencies": False, "executed": False, "load_segments": load_count}
