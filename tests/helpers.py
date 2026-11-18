"""Shared helpers for the test suite: imports app.py and builds synthetic game files."""
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402  (importing does not start the GUI: it is guarded by __main__)


def encrypt_scsc(plain: bytes) -> bytes:
    """Encrypts data the way the game stores encrypted SII files (ScsC: AES-CBC over zlib)."""
    from Crypto.Cipher import AES
    comp = zlib.compress(plain)
    pad = 16 - len(comp) % 16
    comp += bytes([pad]) * pad
    iv = bytes(range(16))
    enc = AES.new(app.SCS_KEY, AES.MODE_CBC, iv).encrypt(comp)
    return app.SCSC_MAGIC + b"\0" * 32 + iv + struct.pack("<I", len(plain)) + enc


def encode_token(s: str) -> int:
    """Inverse of app.decode_token."""
    v = 0
    for ch in reversed(s):
        v = v * 38 + app._TOKEN_CHARS.index(ch) + 1
    return v


class BSIIBuilder:
    """Minimal writer for binary SII (BSII) files, enough to exercise BSIIReader."""

    def __init__(self, version: int = 3):
        self.out = bytearray(b"BSII" + struct.pack("<I", version))

    @staticmethod
    def string(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<I", len(b)) + b

    def struct_def(self, sid: int, name: str, fields: list):
        o = struct.pack("<I", 0) + b"\x01" + struct.pack("<I", sid) + self.string(name)
        for t, fname in fields:
            o += struct.pack("<I", t) + self.string(fname)
        o += struct.pack("<I", 0)
        self.out += o

    def end_defs(self):
        # A struct block with valid = 0 (the game writes one before the data units)
        self.out += struct.pack("<I", 0) + b"\x00"

    def unit(self, sid: int, uid_tokens: list, values: bytes):
        o = struct.pack("<I", sid) + bytes([len(uid_tokens)])
        o += b"".join(struct.pack("<Q", encode_token(t)) for t in uid_tokens)
        self.out += o + values

    def bytes(self) -> bytes:
        return bytes(self.out)


def make_save(money=1000, xp=500, adr=3, skill=2) -> bytes:
    """A small game.sii with a bank unit and an economy unit containing every editable field."""
    b = BSIIBuilder()
    b.struct_def(1, "bank", [(0x31, "money_account"), (0x01, "coinsurance")])
    b.struct_def(2, "economy", [
        (0x27, "experience_points"), (0x27, "adr"), (0x27, "long_dist"), (0x27, "heavy"),
        (0x27, "fragile"), (0x27, "urgent"), (0x27, "mechanical"),
        (0x26, "visited"), (0x39, "bank_link"), (0x05, "game_time"),
    ])
    b.end_defs()
    b.unit(1, ["bank", "player"], struct.pack("<q", money) + BSIIBuilder.string("ok"))
    econ = struct.pack("<7I", xp, adr, skill, skill, skill, skill, skill)
    econ += struct.pack("<I3i", 3, 1, -2, 3)          # visited: int array of 3
    econ += bytes([2]) + struct.pack("<2Q", encode_token("bank"), encode_token("player"))
    econ += struct.pack("<f", 12.5)
    b.unit(2, ["economy"], econ)
    return b.bytes()


PROFILE_SII = (
    "SiiNunit\n{\nuser_profile : _nameless.1 {\n face: 0\n active_mods: 3\n"
    ' active_mods[0]: "mod_a|Mod A"\n active_mods[1]: "mod_b|Mod B"\n active_mods[2]: "mod_c|Mod C"\n'
    ' profile_name: "Test Driver"\n}\n\n}\n'
)

MANIFEST_SII = (
    "SiiNunit\n{\nmod_package : .package_name\n{\n package_version: \"1.0\"\n"
    " display_name: \"\\xd0\\x9c\\xd1\\x96\\xd0\\xb9 \\xd0\\xbc\\xd0\\xbe\\xd0\\xb4\"\n"
    " category[]: \"truck\"\n category[]: \"paint_job\"\n}\n}\n"
)
