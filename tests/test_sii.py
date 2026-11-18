"""Text SII parsing, escaping, ScsC decryption, profile names, folder sizes, game auto-backups."""
import tempfile
import unittest
from pathlib import Path

from helpers import app, encrypt_scsc, PROFILE_SII


class SiiEscapeTests(unittest.TestCase):
    def test_unescape_utf8_hex_and_quotes(self):
        self.assertEqual(app.sii_unescape(r'\xd0\x9a\xd0\xb8\xd1\x97\xd0\xb2 \"FM\"'), 'Київ "FM"')

    def test_escape_roundtrip(self):
        for s in ('Радіо "Рокс"', "plain", "Ünïcødé ✓", ""):
            self.assertEqual(app.sii_unescape(app.sii_escape(s)), s)

    def test_escape_is_ascii_and_replaces_backslash(self):
        out = app.sii_escape('Київ\\FM')
        self.assertTrue(out.isascii())
        self.assertNotIn("\\\\", out)
        self.assertEqual(app.sii_unescape(out), "Київ/FM")


class ParseSiiUnitTests(unittest.TestCase):
    def test_scalars_arrays_and_quotes(self):
        data = app.parse_sii_unit(PROFILE_SII)
        self.assertEqual(data["face"], "0")
        self.assertEqual(data["profile_name"], "Test Driver")
        self.assertEqual(data["active_mods"], ["mod_a|Mod A", "mod_b|Mod B", "mod_c|Mod C"])

    def test_reads_only_first_unit(self):
        text = "SiiNunit\n{\na : x {\n k: 1\n}\nb : y {\n k: 2\n}\n}\n"
        self.assertEqual(app.parse_sii_unit(text), {"k": "1"})

    def test_empty_brackets_array(self):
        text = 'SiiNunit\n{\nm : .p\n{\n category[]: "map"\n category[]: "ui"\n}\n}\n'
        self.assertEqual(app.parse_sii_unit(text)["category"], ["map", "ui"])

    def test_crlf(self):
        data = app.parse_sii_unit(PROFILE_SII.replace("\n", "\r\n"))
        self.assertEqual(data["profile_name"], "Test Driver")


class SiiFloatTests(unittest.TestCase):
    def test_plain_and_hex(self):
        self.assertEqual(app.sii_float("1.5"), 1.5)
        self.assertEqual(app.sii_float("&3fc00000"), 1.5)

    def test_bad_values(self):
        self.assertIsNone(app.sii_float("abc"))
        self.assertIsNone(app.sii_float("&zz"))
        self.assertIsNone(app.sii_float(None))


class ScscTests(unittest.TestCase):
    def test_decrypt_roundtrip(self):
        plain = PROFILE_SII.encode()
        self.assertEqual(app._decrypt_scsc(encrypt_scsc(plain)), plain)

    def test_decrypt_rejects_garbage(self):
        self.assertIsNone(app._decrypt_scsc(b"ScsC" + b"\0" * 80))
        self.assertIsNone(app._decrypt_scsc(b"NOPE" + b"\0" * 80))

    def test_sii_to_text_handles_encrypted(self):
        self.assertIn("Test Driver", app._sii_to_text(encrypt_scsc(PROFILE_SII.encode())))


class ReadSiiFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_with_bom(self):
        p = self.dir / "a.sii"
        p.write_bytes(b"\xef\xbb\xbf" + PROFILE_SII.encode())
        self.assertEqual(app.read_sii_file(p)["profile_name"], "Test Driver")

    def test_encrypted(self):
        p = self.dir / "a.sii"
        p.write_bytes(encrypt_scsc(PROFILE_SII.encode()))
        self.assertEqual(app.read_sii_file(p)["face"], "0")

    def test_binary_and_missing(self):
        p = self.dir / "b.sii"
        p.write_bytes(b"BSII\x03\0\0\0")
        self.assertIsNone(app.read_sii_file(p))
        self.assertIsNone(app.read_sii_file(self.dir / "missing.sii"))


class ProfileNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_decode_folder_hex_name(self):
        self.assertEqual(app.decode_folder_hex_name("54657374"), "Test")
        self.assertEqual(app.decode_folder_hex_name("d09fd0b5d182d180d0be"), "Петро")
        self.assertEqual(app.decode_folder_hex_name("zz"), "")
        self.assertEqual(app.decode_folder_hex_name("0102"), "")

    def test_from_profile_sii_plain_and_encrypted(self):
        prof = self.dir / "54657374"
        prof.mkdir()
        (prof / "profile.sii").write_text(PROFILE_SII, encoding="utf-8")
        self.assertEqual(app.read_profile_name(prof), "Test Driver")
        (prof / "profile.sii").write_bytes(encrypt_scsc(PROFILE_SII.encode()))
        self.assertEqual(app.read_profile_name(prof), "Test Driver")

    def test_cloud_copy_wins(self):
        prof, cloud = self.dir / "p", self.dir / "cloud"
        prof.mkdir()
        cloud.mkdir()
        (prof / "profile.sii").write_text(PROFILE_SII, encoding="utf-8")
        (cloud / "profile.sii").write_text(PROFILE_SII.replace("Test Driver", "Cloud"), encoding="utf-8")
        self.assertEqual(app.read_profile_name(prof, cloud), "Cloud")

    def test_fallback_to_folder_name(self):
        hexdir = self.dir / "54657374"
        hexdir.mkdir()
        self.assertEqual(app.read_profile_name(hexdir), "Test")
        plain = self.dir / "plainname"
        plain.mkdir()
        self.assertEqual(app.read_profile_name(plain), "plainname")


class SizeAndBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fmt_size(self):
        self.assertEqual(app.fmt_size(2048), "2.0 KB")
        self.assertEqual(app.fmt_size(5 * 1024 * 1024 + 1), "5.0 MB")
        self.assertEqual(app.fmt_size(3 * 1024 ** 3 + 1), "3.00 GB")

    def test_get_dir_size(self):
        (self.dir / "sub").mkdir()
        (self.dir / "a").write_bytes(b"x" * 10)
        (self.dir / "sub" / "b").write_bytes(b"x" * 5)
        self.assertEqual(app.get_dir_size(self.dir), 15)
        self.assertEqual(app.get_dir_size(self.dir / "a"), 10)
        self.assertEqual(app.get_dir_size(self.dir / "missing"), 0)
        self.assertEqual(app.get_dir_size_str(self.dir / "missing"), "0 KB")

    def test_game_auto_backups_newest_first(self):
        for name in ("steam_profiles(1.56.1.7s).bak", "profiles(1.60.0.1s).bak", "profiles", "x(1.0).bak"):
            (self.dir / name).mkdir()
        (self.dir / "profiles(1.61.0.0s).bak").write_text("file, not dir")
        found = app.list_game_auto_backups(self.dir)
        self.assertEqual([b["version"] for b in found], ["1.60.0.1s", "1.56.1.7s"])
        self.assertEqual(app.list_game_auto_backups(self.dir / "missing"), [])


if __name__ == "__main__":
    unittest.main()
