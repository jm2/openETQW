#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Print the engine's checksum of each pk4, as the retail-install check compares it.

The value is idFileSystemLocal::LoadPackFile's pack checksum
(src/framework/FileSystem.cpp), which the log prints as "Loaded pk4 ... with
checksum" and which docs/official-pk4-checksums.md tabulates:
  - walk the zip central directory in order;
  - for every entry whose uncompressed size is > 0, append its CRC-32 from the
    central directory as a little-endian 32-bit word;
  - MD4 over that buffer, folded to 32 bits by XOR of the digest's four
    little-endian words (MD4_BlockChecksum, the game repo's src/idlib/hashing/MD4.cpp).
Only the central directory is read; file data is not hashed. With --sha256 it
also prints each file's SHA-256, for byte-identity comparisons.

Usage: pk4_checksum.py [--sha256] <file.pk4>...
"""
import hashlib
import os
import struct
import sys
import zipfile


def _lrot(x, n):
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def md4(data: bytes) -> bytes:
    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    ml = len(data) * 8
    data = data + b"\x80"
    data += b"\x00" * ((56 - len(data) % 64) % 64)
    data += struct.pack("<Q", ml & 0xFFFFFFFFFFFFFFFF)
    F = lambda x, y, z: (x & y) | (~x & z)
    G = lambda x, y, z: (x & y) | (x & z) | (y & z)
    H = lambda x, y, z: x ^ y ^ z
    for off in range(0, len(data), 64):
        X = list(struct.unpack("<16I", data[off:off + 64]))
        aa, bb, cc, dd = a, b, c, d
        for i in (0, 4, 8, 12):
            a = _lrot(a + F(b, c, d) + X[i], 3)
            d = _lrot(d + F(a, b, c) + X[i + 1], 7)
            c = _lrot(c + F(d, a, b) + X[i + 2], 11)
            b = _lrot(b + F(c, d, a) + X[i + 3], 19)
        for i in (0, 1, 2, 3):
            a = _lrot(a + G(b, c, d) + X[i] + 0x5A827999, 3)
            d = _lrot(d + G(a, b, c) + X[i + 4] + 0x5A827999, 5)
            c = _lrot(c + G(d, a, b) + X[i + 8] + 0x5A827999, 9)
            b = _lrot(b + G(c, d, a) + X[i + 12] + 0x5A827999, 13)
        for i in (0, 2, 1, 3):
            a = _lrot(a + H(b, c, d) + X[i] + 0x6ED9EBA1, 3)
            d = _lrot(d + H(a, b, c) + X[i + 8] + 0x6ED9EBA1, 9)
            c = _lrot(c + H(d, a, b) + X[i + 4] + 0x6ED9EBA1, 11)
            b = _lrot(b + H(c, d, a) + X[i + 12] + 0x6ED9EBA1, 15)
        a = (a + aa) & 0xFFFFFFFF
        b = (b + bb) & 0xFFFFFFFF
        c = (c + cc) & 0xFFFFFFFF
        d = (d + dd) & 0xFFFFFFFF
    return struct.pack("<4I", a, b, c, d)


def md4_block_checksum(data: bytes) -> int:
    w = struct.unpack("<4I", md4(data))
    return w[0] ^ w[1] ^ w[2] ^ w[3]


def pk4_checksum(path: str):
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()  # central-directory order
        crcs = [zi.CRC for zi in infos if zi.file_size > 0]
    buf = b"".join(struct.pack("<I", c & 0xFFFFFFFF) for c in crcs)
    return md4_block_checksum(buf), len(infos), len(crcs)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv):
    # self-test of MD4 against RFC 1320 vectors
    assert md4(b"").hex() == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert md4(b"abc").hex() == "a448017aaf21d8525fc10ae87aa6729d"
    assert md4(b"12345678901234567890123456789012345678901234567890123456789012345678901234567890").hex() == "e33b4ddc9c38f2199c3e7b164fcc0536"
    want_sha = "--sha256" in argv
    for p in [a for a in argv if not a.startswith("--")]:
        ck, n, nz = pk4_checksum(p)
        line = f"{os.path.basename(p)}\t0x{ck:08x}\tentries={n}\tnonempty={nz}\tsize={os.path.getsize(p)}"
        if want_sha:
            line += f"\tsha256={sha256(p)}"
        print(line, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
