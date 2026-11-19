"""Post-export fix: rename block IDs WorldPainter writes with their pre-1.20.3 names.

WP 2.27.1 (format org.pepsoft.anvil.1.20.5, DataVersion 3837) still writes `minecraft:grass`; since 1.20.3
that block is `minecraft:short_grass`, and Minecraft does not datafix it at this DataVersion (block is lost).
Streams one region file at a time; only chunks whose NBT contains an old name are parsed and re-encoded.
"""
import io
import os
import struct
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import nbtlib

RENAMES = {"minecraft:grass": "minecraft:short_grass"}
# NBT strings are length-prefixed, so this matches "minecraft:grass" but not "minecraft:grass_block"
_NEEDLES = [struct.pack(">H", len(k)) + k.encode() for k in RENAMES]


def _rename(chunk) -> int:
    n = 0
    for sec in chunk.get("sections", []):
        for b in sec.get("block_states", {}).get("palette", []):
            new = RENAMES.get(str(b["Name"]))
            if new:
                b["Name"] = nbtlib.String(new)
                n += 1
    return n


def _raw_chunks(raw: bytes):
    """Yield (slot, zlib payload) of every chunk in a region file's bytes."""
    for slot in range(1024):
        off, = struct.unpack(">I", b"\0" + raw[slot * 4:slot * 4 + 3])
        if off == 0:
            continue
        length, comp = struct.unpack(">IB", raw[off * 4096:off * 4096 + 5])
        if comp != 2:
            raise ValueError(f"unsupported compression {comp} (slot {slot})")
        yield slot, raw[off * 4096 + 5:off * 4096 + 4 + length]


def _write_region(path: Path, chunks: list, timestamps: bytes = bytes(4096)) -> None:
    """Rewrite a region file from (slot, zlib payload) pairs."""
    header, body = bytearray(4096), bytearray()
    for slot, data in chunks:
        blob = struct.pack(">IB", len(data) + 1, 2) + data
        blob += b"\0" * (-len(blob) % 4096)
        offset = 2 + len(body) // 4096
        header[slot * 4:slot * 4 + 4] = struct.pack(">I", (offset << 8) | (len(blob) // 4096))
        body += blob
    path.write_bytes(bytes(header) + timestamps + bytes(body))


def fix_region(rf: Path) -> int:
    raw = rf.read_bytes()
    out, total = [], 0
    for slot, data in _raw_chunks(raw):
        nbt = zlib.decompress(data)
        if any(nd in nbt for nd in _NEEDLES):
            chunk = nbtlib.File.parse(io.BytesIO(nbt))
            n = _rename(chunk)
            if n:
                buf = io.BytesIO()
                chunk.write(buf)
                data = zlib.compress(buf.getvalue())
                total += n
        out.append((slot, data))
    if total:
        _write_region(rf, out, raw[4096:8192])
    return total


def fix_world(world: Path) -> int:
    """Rename legacy block IDs in every region file. Returns number of palette entries changed.
    Region files are independent: several are fixed at once in worker processes (one region each)."""
    files = sorted((world / "region").glob("r.*.mca"))
    workers = min(len(files), max(1, min(8, (os.cpu_count() or 2) // 2)))
    if workers <= 2:
        return sum(fix_region(rf) for rf in files)
    with ProcessPoolExecutor(workers) as ex:
        return sum(ex.map(fix_region, files))
