"""Keccak-256, in pure Python, because the repo ships with no dependencies.

`hashlib.sha3_256` is SHA-3, which pads with 0x06. Ethereum pads with 0x01, so a
selector computed from hashlib is silently wrong for every contract call. This is
the only reason the file exists: function selectors and event topics have to be
computed, not pasted, or a review cannot check them.

Vectors are in tests/test_keccak.py, including the two selectors the agent uses.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1

# iota: one round constant per round, 24 rounds for Keccak-f[1600].
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# rho: rotation offset indexed [x][y].
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl(value: int, shift: int) -> int:
    shift &= 63
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _permute(state: list) -> None:
    """Keccak-f[1600] in place. state is a 5x5 list of 64-bit lanes."""
    for rc in _RC:
        # theta
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            dx = d[x]
            for y in range(5):
                state[x][y] ^= dx

        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(state[x][y], _ROT[x][y])

        # chi
        for x in range(5):
            bx, bx1, bx2 = b[x], b[(x + 1) % 5], b[(x + 2) % 5]
            for y in range(5):
                state[x][y] = bx[y] ^ ((~bx1[y]) & bx2[y])

        # iota
        state[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """The Ethereum keccak256: original Keccak padding, 136-byte rate, 32-byte digest."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] ^= 0x80

    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for lane in range(rate // 8):
            x, y = lane % 5, lane // 5
            state[x][y] ^= int.from_bytes(block[lane * 8:lane * 8 + 8], "little")
        _permute(state)

    out = bytearray()
    for lane in range(4):
        x, y = lane % 5, lane // 5
        out += state[x][y].to_bytes(8, "little")
    return bytes(out)


def selector(signature: str) -> bytes:
    """First four bytes of keccak256 over a canonical signature, e.g. 'transfer(address,uint256)'."""
    return keccak256(signature.encode("ascii"))[:4]


def topic(signature: str) -> bytes:
    """The 32-byte event topic for a canonical event signature."""
    return keccak256(signature.encode("ascii"))
