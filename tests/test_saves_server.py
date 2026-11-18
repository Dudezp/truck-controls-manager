"""Binary SII (BSII) save reader/patcher and convoy server_config.sii."""
import struct
import tempfile
import unittest
from pathlib import Path

from helpers import app, encrypt_scsc, encode_token, make_save, BSIIBuilder


class TokenTests(unittest.TestCase):
    def test_roundtrip(self):
        for s in ("bank", "player", "economy", "a_1", "z"):
            self.assertEqual(app.decode_token(encode_token(s)), s)
        self.assertEqual(app.decode_token(0), "")


class BSIIReaderTests(unittest.TestCase):
    def test_units_and_values(self):
        units = list(app.BSIIReader(make_save()).units())
        self.assertEqual([(n, uid) for n, uid, _ in units], [("bank", "bank.player"), ("economy", "economy")])
        bank, econ = units[0][2], units[1][2]
        self.assertEqual(bank["money_account"][0], 1000)
        self.assertEqual(bank["coinsurance"][0], "ok")
        self.assertEqual(econ["visited"][0], [1, -2, 3])
        self.assertEqual(econ["bank_link"][0], "bank.player")
        self.assertAlmostEqual(econ["game_time"][0], 12.5)

    def test_rejects_non_bsii_and_truncated(self):
        with self.assertRaises(app.BSIIError):
            app.BSIIReader(b"SiiNunit")
        with self.assertRaises(app.BSIIError):
            list(app.BSIIReader(make_save()[:-3]).units())

    def test_unknown_structure_and_trailing(self):
        b = BSIIBuilder()
        b.unit(9, ["x"], b"")
        with self.assertRaises(app.BSIIError):
            list(app.BSIIReader(b.bytes()).units())

    def test_nameless_id(self):
        b = BSIIBuilder()
        b.struct_def(1, "thing", [(0x25, "v")])
        b.out += struct.pack("<I", 1) + b"\xff" + struct.pack("<Q", 0x0000_1234_5678_9ABC) + struct.pack("<i", -7)
        (_n, uid, vals), = app.BSIIReader(b.bytes()).units()
        self.assertEqual(uid, "_nameless.1234.5678.9abc")
        self.assertEqual(vals["v"][0], -7)


class SaveFieldsTests(unittest.TestCase):
    def test_read(self):
        f = app.read_save_fields(make_save(money=42, xp=9000, adr=5))
        self.assertEqual((f["money_account"][0], f["experience_points"][0], f["adr"][0]), (42, 9000, 5))
        self.assertEqual(set(f), set(app.SAVE_EDIT_FIELDS))

    def test_patch_changes_only_target_bytes(self):
        data = make_save()
        out = app.patch_save_fields(data, {"money_account": 5_000_000, "heavy": 6})
        self.assertEqual(len(out), len(data))
        f = app.read_save_fields(out)
        self.assertEqual((f["money_account"][0], f["heavy"][0], f["fragile"][0]), (5_000_000, 6, 2))
        self.assertLessEqual(sum(a != b for a, b in zip(out, data)), 16)

    def test_patch_negative_money(self):
        out = app.patch_save_fields(make_save(), {"money_account": -5000})
        self.assertEqual(app.read_save_fields(out)["money_account"][0], -5000)

    def test_patch_range_checked(self):
        with self.assertRaises(ValueError):
            app.patch_save_fields(make_save(), {"adr": 64})
        with self.assertRaises(ValueError):
            app.patch_save_fields(make_save(), {"urgent": -1})

    def test_missing_field(self):
        b = BSIIBuilder()
        b.struct_def(1, "bank", [(0x31, "money_account")])
        b.unit(1, ["bank"], struct.pack("<q", 1))
        with self.assertRaises(app.BSIIError):
            app.read_save_fields(b.bytes())

    def test_load_save_data(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "game.sii"
            p.write_bytes(encrypt_scsc(make_save()))
            self.assertEqual(app.load_save_data(p), make_save())
            p.write_bytes(b"SiiNunit\n{\n}\n")
            with self.assertRaises(app.BSIIError):
                app.load_save_data(p)


SERVER_SII = (
    "SiiNunit\n{\nserver_config : _nameless.123 {\n"
    ' lobby_name: "My lobby"\n description: ""\n max_players: 8\n traffic: true\n'
    " connection_virtual_port: 100\n query_virtual_port: 101\n"
    " connection_dedicated_port: 27015\n query_dedicated_port: 27016\n"
    " moderator_list: 1\n moderator_list[0]: 76561190000000001\n custom_unknown: 42\n}\n\n}\n"
)


class ServerConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "server_config.sii"
        self.path.write_bytes(SERVER_SII.replace("\n", "\r\n").encode())

    def tearDown(self):
        self.tmp.cleanup()

    def test_read(self):
        v = app.read_server_config(self.path)
        self.assertEqual(v["lobby_name"], "My lobby")
        self.assertEqual(v["max_players"], 8)
        self.assertIs(v["traffic"], True)
        self.assertEqual(v["moderator_list"], ["76561190000000001"])
        self.assertNotIn("custom_unknown", v)

    def test_validate(self):
        v = app.read_server_config(self.path)
        self.assertEqual(app.validate_server_config(v), [])
        v.update(lobby_name="Я" * 32, max_players=9, moderator_list=["abc"], query_virtual_port=100)
        errs = {(k, kind) for k, kind, _ in app.validate_server_config(v)}
        self.assertEqual(errs, {("lobby_name", "len"), ("max_players", "range"),
                                ("moderator_list", "ids"), ("query_virtual_port", "same_port")})

    def test_write_roundtrip_keeps_unknown_lines(self):
        v = app.read_server_config(self.path)
        v.update(lobby_name='Конвой "UA"', traffic=False, password="secret",
                 moderator_list=["1", "2"], max_players=4)
        app.write_server_config(self.path, v)
        raw = self.path.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        text = raw.decode()
        self.assertIn("custom_unknown: 42", text)
        back = app.read_server_config(self.path)
        for k in ("lobby_name", "traffic", "password", "moderator_list", "max_players"):
            self.assertEqual(back[k], v[k], k)
        self.assertEqual(text.count("moderator_list:"), 1)


if __name__ == "__main__":
    unittest.main()
