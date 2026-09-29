"""Dependency-free QR encoder (ISO/IEC 18004): byte mode, versions 1-10, error correction level L or M,
automatic mask selection by penalty score. Enough for a pairing URL (~40-100 characters).

Verified against ``zbarimg`` in tests/test_remote.py when it is installed.
"""

from __future__ import annotations

from dataclasses import dataclass

# (ec codewords per block, [(blocks, data codewords per block), ...]) per version 1..10
_EC_TABLE: dict[str, list[tuple[int, list[tuple[int, int]]]]] = {
    "L": [
        (7, [(1, 19)]), (10, [(1, 34)]), (15, [(1, 55)]), (20, [(1, 80)]), (26, [(1, 108)]),
        (18, [(2, 68)]), (20, [(2, 78)]), (24, [(2, 97)]), (30, [(2, 116)]), (18, [(2, 68), (2, 69)]),
    ],
    "M": [
        (10, [(1, 16)]), (16, [(1, 28)]), (26, [(1, 44)]), (18, [(2, 32)]), (24, [(2, 43)]),
        (16, [(4, 27)]), (18, [(4, 31)]), (22, [(2, 38), (2, 39)]), (22, [(3, 36), (2, 37)]), (26, [(4, 43), (1, 44)]),
    ],
}
_TOTAL_CODEWORDS = [26, 44, 70, 100, 134, 172, 196, 242, 292, 346]
_REMAINDER_BITS = [0, 7, 7, 7, 7, 7, 0, 0, 0, 0]
_ALIGNMENT = [[], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]]
_EC_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
_G15 = 0b10100110111
_G15_MASK = 0b101010000010010
_G18 = 0b1111100100101
MAX_VERSION = 10

# GF(256) with the QR primitive polynomial x^8 + x^4 + x^3 + x^2 + 1 (0x11D)
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gf_mul(c, _EXP[i])
        g = ng
    return g


def _rs_encode(data: list[int], n_ec: int) -> list[int]:
    gen = _rs_generator(n_ec)
    rem = [0] * n_ec
    for d in data:
        factor = d ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for j in range(n_ec):
                rem[j] ^= _gf_mul(gen[j + 1], factor)
    return rem


def _bch(data: int, gen: int, shift: int) -> int:
    d = data << shift
    glen = gen.bit_length()
    while d.bit_length() >= glen:
        d ^= gen << (d.bit_length() - glen)
    return (data << shift) | d


def capacity(version: int, level: str) -> int:
    """Maximum bytes (byte mode) for ``version``/``level``."""
    ec, blocks = _EC_TABLE[level][version - 1]
    data_cw = sum(n * k for n, k in blocks)
    count_bits = 8 if version <= 9 else 16
    return (data_cw * 8 - 4 - count_bits) // 8


def choose_version(n_bytes: int, level: str = "M") -> int:
    for v in range(1, MAX_VERSION + 1):
        if capacity(v, level) >= n_bytes:
            return v
    raise ValueError(f"data too long for QR up to version {MAX_VERSION}-{level}: {n_bytes} bytes")


_MASKS = [
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i * j) % 3 + (i + j) % 2) % 2 == 0,
]


@dataclass
class QrCode:
    version: int
    level: str
    mask: int
    modules: list[list[bool]]  # modules[row][col], True = dark

    @property
    def size(self) -> int:
        return len(self.modules)

    def rows(self) -> list[str]:
        """One string per row, '1' dark / '0' light."""
        return ["".join("1" if m else "0" for m in row) for row in self.modules]

    def runs(self) -> list[list[list[int]]]:
        """Per row, the [start, length] of every dark run (compact form for drawing rectangles)."""
        out: list[list[list[int]]] = []
        for row in self.modules:
            r: list[list[int]] = []
            j = 0
            while j < len(row):
                if row[j]:
                    k = j
                    while k < len(row) and row[k]:
                        k += 1
                    r.append([j, k - j])
                    j = k
                else:
                    j += 1
            out.append(r)
        return out

    def to_pbm(self, scale: int = 4, border: int = 4) -> bytes:
        """Binary PBM (P4) image, for zbarimg and friends."""
        n = self.size + 2 * border
        width = n * scale
        lines = []
        for r in range(n):
            bits = []
            for c in range(n):
                dark = border <= r < n - border and border <= c < n - border and self.modules[r - border][c - border]
                bits.extend([1 if dark else 0] * scale)
            row = bytearray()
            for i in range(0, width, 8):
                chunk = bits[i:i + 8] + [0] * (8 - len(bits[i:i + 8]))
                row.append(int("".join(map(str, chunk)), 2))
            lines.append(bytes(row) * 1)
        body = b"".join(line * 1 for line in lines for _ in range(scale))
        return b"P4\n%d %d\n" % (width, n * scale) + body

    def to_text(self, dark: str = "██", light: str = "  ", border: int = 2) -> str:
        n = self.size
        out = []
        for r in range(-border, n + border):
            line = []
            for c in range(-border, n + border):
                line.append(dark if 0 <= r < n and 0 <= c < n and self.modules[r][c] else light)
            out.append("".join(line))
        return "\n".join(out)


class _Matrix:
    def __init__(self, version: int):
        self.n = version * 4 + 17
        self.version = version
        self.m: list[list[bool | None]] = [[None] * self.n for _ in range(self.n)]
        self.func: list[list[bool]] = [[False] * self.n for _ in range(self.n)]

    def set_func(self, r: int, c: int, dark: bool) -> None:
        self.m[r][c] = dark
        self.func[r][c] = True

    def finder(self, r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < self.n and 0 <= cc < self.n:
                    inside = 0 <= r <= 6 and 0 <= c <= 6
                    dark = inside and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                    self.set_func(rr, cc, dark)

    def place_function_patterns(self) -> None:
        n = self.n
        self.finder(0, 0)
        self.finder(0, n - 7)
        self.finder(n - 7, 0)
        for i in range(8, n - 8):
            self.set_func(6, i, i % 2 == 0)
            self.set_func(i, 6, i % 2 == 0)
        pos = _ALIGNMENT[self.version - 1]
        corners = {(pos[0], pos[0]), (pos[0], pos[-1]), (pos[-1], pos[0])} if pos else set()
        for r in pos:
            for c in pos:
                if (r, c) in corners:  # would overlap a finder pattern
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        dark = max(abs(dr), abs(dc)) != 1
                        self.set_func(r + dr, c + dc, dark)
        # reserve format info areas (values written later)
        for i in range(9):
            if i != 6:
                self.set_func(8, i, False)
                self.set_func(i, 8, False)
        for i in range(8):
            self.set_func(8, n - 1 - i, False)
            self.set_func(n - 1 - i, 8, False)
        self.set_func(n - 8, 8, True)  # dark module
        if self.version >= 7:
            for i in range(18):
                self.set_func(i // 3, i % 3 + n - 11, False)
                self.set_func(i % 3 + n - 11, i // 3, False)

    def write_format(self, level: str, mask: int) -> None:
        n = self.n
        bits = _bch((_EC_BITS[level] << 3) | mask, _G15, 10) ^ _G15_MASK
        for i in range(15):
            b = (bits >> i) & 1 == 1
            if i < 6:
                self.m[i][8] = b
            elif i < 8:
                self.m[i + 1][8] = b
            else:
                self.m[n - 15 + i][8] = b
            if i < 8:
                self.m[8][n - 1 - i] = b
            elif i < 9:
                self.m[8][15 - i] = b
            else:
                self.m[8][14 - i] = b
        if self.version >= 7:
            vbits = _bch(self.version, _G18, 12)
            for i in range(18):
                b = (vbits >> i) & 1 == 1
                self.m[i // 3][i % 3 + n - 11] = b
                self.m[i % 3 + n - 11][i // 3] = b

    def place_data(self, data: bytes, mask: int) -> None:
        n = self.n
        f = _MASKS[mask]
        bit = 7
        idx = 0
        inc = -1
        row = n - 1
        col = n - 1
        while col > 0:
            if col == 6:
                col -= 1
            while True:
                for c in (col, col - 1):
                    if not self.func[row][c]:
                        dark = idx < len(data) and (data[idx] >> bit) & 1 == 1
                        if f(row, c):
                            dark = not dark
                        self.m[row][c] = dark
                        bit -= 1
                        if bit < 0:
                            bit = 7
                            idx += 1
                row += inc
                if row < 0 or row >= n:
                    row -= inc
                    inc = -inc
                    break
            col -= 2

    def penalty(self) -> int:
        n = self.n
        m = [[bool(x) for x in row] for row in self.m]
        score = 0
        for lines in (m, [list(col) for col in zip(*m, strict=True)]):
            for line in lines:
                run = 1
                for i in range(1, n):
                    if line[i] == line[i - 1]:
                        run += 1
                    else:
                        if run >= 5:
                            score += 3 + run - 5
                        run = 1
                if run >= 5:
                    score += 3 + run - 5
                # finder-like patterns
                s = "".join("1" if x else "0" for x in line)
                score += 40 * (s.count("10111010000") + s.count("00001011101"))
        for r in range(n - 1):
            for c in range(n - 1):
                v = m[r][c]
                if m[r][c + 1] == v and m[r + 1][c] == v and m[r + 1][c + 1] == v:
                    score += 3
        dark = sum(sum(row) for row in m)
        ratio = dark * 100 // (n * n)
        prev = ratio - ratio % 5
        score += min(abs(prev - 50) // 5, abs(prev + 5 - 50) // 5) * 10
        return score


def _encode_data(payload: bytes, version: int, level: str) -> bytes:
    ec, blocks = _EC_TABLE[level][version - 1]
    data_cw = sum(n * k for n, k in blocks)
    count_bits = 8 if version <= 9 else 16
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)
    put(len(payload), count_bits)
    for b in payload:
        put(b, 8)
    put(0, min(4, data_cw * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(codewords) < data_cw:
        codewords.append(pad[i % 2])
        i += 1
    # split into blocks, compute EC, interleave
    data_blocks: list[list[int]] = []
    pos = 0
    for n_blocks, k in blocks:
        for _ in range(n_blocks):
            data_blocks.append(codewords[pos:pos + k])
            pos += k
    ec_blocks = [_rs_encode(b, ec) for b in data_blocks]
    out: list[int] = []
    for i in range(max(len(b) for b in data_blocks)):
        for b in data_blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec):
        for b in ec_blocks:
            out.append(b[i])
    assert len(out) == _TOTAL_CODEWORDS[version - 1]
    return bytes(out)


def encode(text: str | bytes, level: str = "M", version: int | None = None, mask: int | None = None) -> QrCode:
    """Encode ``text`` (UTF-8) as a QR symbol. The smallest version that fits is used unless ``version`` is given."""
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if level not in ("L", "M"):
        raise ValueError("level must be L or M")
    v = version or choose_version(len(payload), level)
    if not 1 <= v <= MAX_VERSION or capacity(v, level) < len(payload):
        raise ValueError("data does not fit in the requested version")
    data = _encode_data(payload, v, level)
    best: tuple[int, int, _Matrix] | None = None
    for mk in ([mask] if mask is not None else range(8)):
        mat = _Matrix(v)
        mat.place_function_patterns()
        mat.place_data(data, mk)
        mat.write_format(level, mk)
        score = mat.penalty()
        if best is None or score < best[0]:
            best = (score, mk, mat)
    assert best is not None
    return QrCode(version=v, level=level, mask=best[1], modules=[[bool(x) for x in row] for row in best[2].m])
