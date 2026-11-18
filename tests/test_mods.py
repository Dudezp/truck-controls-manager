"""Mod manifests, installed-mod scan, classification, auto-sort plan and profile.sii reordering."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from helpers import app, encrypt_scsc, build_hashfs_v1, build_hashfs_v2, PROFILE_SII, MANIFEST_SII


def make_zip(path: Path, files: dict):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_manifest(self):
        d = self.dir / "mymod"
        d.mkdir()
        (d / "manifest.sii").write_text(MANIFEST_SII, encoding="utf-8")
        self.assertEqual(app.read_mod_display_name(d), "Мій мод")
        self.assertEqual(app.read_mod_categories(d), ["truck", "paint_job"])

    def test_zip_and_encrypted_manifest(self):
        z = self.dir / "m.scs"
        make_zip(z, {"manifest.sii": encrypt_scsc(MANIFEST_SII.encode())})
        self.assertEqual(app.read_mod_display_name(z), "Мій мод")
        self.assertEqual(app.read_mod_categories(z), ["truck", "paint_job"])

    def test_hashfs_and_no_manifest(self):
        h = self.dir / "hash.scs"
        h.write_bytes(b"SCS#" + b"\0" * 100)
        self.assertIsNone(app.read_mod_display_name(h))
        self.assertEqual(app.read_mod_categories(h), [])
        z = self.dir / "empty.zip"
        make_zip(z, {"def/x.sii": "x"})
        self.assertIsNone(app.read_mod_display_name(z))

    def test_localized_name_ignored(self):
        d = self.dir / "loc"
        d.mkdir()
        (d / "manifest.sii").write_text(MANIFEST_SII.replace(
            MANIFEST_SII.split('display_name: ')[1].split("\n")[0], '"@@mod_name@@"'), encoding="utf-8")
        self.assertIsNone(app.read_mod_display_name(d))

    def test_workshop_latest_wins(self):
        ws = self.dir / "123456"
        (ws / "100_content").mkdir(parents=True)
        (ws / "100_content" / "manifest.sii").write_text(
            MANIFEST_SII.replace("\\xd0\\x9c\\xd1\\x96\\xd0\\xb9 \\xd0\\xbc\\xd0\\xbe\\xd0\\xb4", "Old"), encoding="utf-8")
        make_zip(ws / "latest.zip", {"manifest.sii": MANIFEST_SII.replace(
            "\\xd0\\x9c\\xd1\\x96\\xd0\\xb9 \\xd0\\xbc\\xd0\\xbe\\xd0\\xb4", "New")})
        self.assertEqual(app.read_mod_display_name(ws), "New")


FULL_MANIFEST = MANIFEST_SII.replace(
    ' category[]: "truck"',
    ' author: "Автор"\n icon: "mod_icon.jpg"\n description_file: "mod_description.txt"\n'
    ' compatible_versions[]: "1.62.*"\n category[]: "truck"')
LONG_PATH = "def/vehicle/truck/some_brand.some_model/accessory/very_long_folder_name/item.sii"


class CityHashTests(unittest.TestCase):
    def test_matches_real_hashfs_paths(self):
        # Hashes taken from a real HashFS v1 mod archive: one string per length branch of CityHash64
        cases = {
            "": 0x9ae16a3b2f90404f,
            "def": 0x2c6f469efb31c45a,
            "automat": 0x56bc42eecbc73f2f,
            "actros_mp6": 0xe0ea01884705607f,
            "unit/hookup/vehicle": 0x43374764a0f107f8,
            "unit/hookup/vehicle/flare/blinker_led.sui": 0xef2fcb301ac4111d,
            "unit/hookup/vehicle/flare/vehicle_spot_end_outline_front_white.sui": 0x2ea77845c7c4363,
        }
        for s, h in cases.items():
            self.assertEqual(app.cityhash64(s.encode()), h, s)


class HashFSTests(unittest.TestCase):
    FILES = {
        "manifest.sii": FULL_MANIFEST.encode(),
        "mod_icon.jpg": b"\xff\xd8JPEG",
        "mod_description.txt": "Опис мода\n".encode("utf-8"),
        "def/a.sii": b"x" * 1000,
        LONG_PATH: b"long",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check_archive(self, path, version):
        self.assertEqual(app.mod_archive_kind(path), f"hashfs{version}")
        with app.HashFSReader(path) as h:
            self.assertEqual(h.version, version)
            for name, data in self.FILES.items():
                self.assertEqual(h.read(name), data, name)
                self.assertEqual(h.read("/" + name), data, name)
            self.assertIsNone(h.read("nope.txt"))
            self.assertIsNone(h.read("def"))          # directory, not a file
            dirs, files = h.listdir("")
            self.assertEqual(sorted(dirs), ["def"])
            self.assertIn("manifest.sii", files)
            self.assertEqual(h.listdir("def")[0], ["vehicle"])
            self.assertEqual(h.listdir("missing"), ([], []))
        info = app.read_mod_info(path)
        self.assertEqual(info["display_name"], "Мій мод")
        self.assertEqual(info["author"], "Автор")
        self.assertEqual(info["package_version"], "1.0")
        self.assertEqual(info["categories"], ["truck", "paint_job"])
        self.assertEqual(info["compatible_versions"], ["1.62.*"])
        self.assertEqual(info["icon"], b"\xff\xd8JPEG")
        self.assertEqual(info["description"], "Опис мода")
        self.assertEqual(app.read_mod_display_name(path), "Мій мод")
        self.assertEqual(app.read_mod_categories(path), ["truck", "paint_job"])

    def test_v1_compressed_and_stored(self):
        p = self.dir / "v1.scs"
        p.write_bytes(build_hashfs_v1(self.FILES))
        self.check_archive(p, 1)
        p2 = self.dir / "v1raw.scs"
        p2.write_bytes(build_hashfs_v1(self.FILES, compress=False))
        self.check_archive(p2, 1)

    def test_v2(self):
        p = self.dir / "v2.scs"
        p.write_bytes(build_hashfs_v2(self.FILES))
        self.check_archive(p, 2)

    def test_broken_and_other_formats(self):
        bad = self.dir / "bad.scs"
        bad.write_bytes(b"SCS#\x02\x00\x00\x00CITY" + b"\xff" * 60)
        with self.assertRaises(app.HashFSError):
            app.HashFSReader(bad)
        self.assertIsNone(app.open_mod_archive(bad))
        self.assertIsNone(app.read_mod_display_name(bad))
        enc = self.dir / "enc.scs"
        enc.write_bytes(b"AEM!" + b"\0" * 50)
        self.assertEqual(app.mod_archive_kind(enc), "encrypted")
        self.assertEqual(app.read_mod_info(enc)["kind"], "encrypted")
        self.assertEqual(app.mod_archive_kind(self.dir), "folder")

    def test_cp1251_description_and_zip_info(self):
        z = self.dir / "m.zip"
        make_zip(z, {"manifest.sii": FULL_MANIFEST, "MOD_ICON.JPG": b"img",
                     "mod_description.txt": "Опис".encode("cp1251")})
        info = app.read_mod_info(z)
        self.assertEqual(info["kind"], "zip")
        self.assertEqual(info["icon"], b"img")         # імена в ZIP без урахування регістру
        self.assertEqual(info["description"], "Опис")

    def test_workshop_hashfs_version(self):
        ws = self.dir / "777"
        ws.mkdir()
        (ws / "universal.scs").write_bytes(build_hashfs_v2(self.FILES))
        info = app.read_mod_info(ws)
        self.assertEqual(info["kind"], "hashfs2")
        self.assertEqual(info["source_file"], ws / "universal.scs")
        self.assertEqual(info["display_name"], "Мій мод")


class ScanInstalledModsTests(unittest.TestCase):
    def test_local_and_workshop(self):
        with tempfile.TemporaryDirectory() as t:
            game, ws = Path(t) / "game", Path(t) / "ws"
            (game / "mod" / "folder_mod").mkdir(parents=True)
            make_zip(game / "mod" / "a.scs", {"manifest.sii": MANIFEST_SII})
            make_zip(game / "mod" / "b.zip", {"x": "y"})
            (game / "mod" / "notes.txt").write_text("skip")
            (ws / "123456").mkdir(parents=True)
            (ws / "readme").mkdir()
            with mock.patch.object(app, "find_workshop_dir", return_value=ws), \
                    mock.patch.object(app, "read_workshop_sizes", return_value={"123456": 777}):
                mods = app.scan_installed_mods("Euro Truck Simulator 2", game)
        self.assertEqual(set(mods), {"a", "b", "folder_mod", "mod_workshop_package.000000000001E240"})
        self.assertEqual(mods["a"]["name"], "Мій мод")
        self.assertEqual(mods["a"]["source"], "local")
        ws_mod = mods["mod_workshop_package.000000000001E240"]
        self.assertEqual((ws_mod["source"], ws_mod["ws_id"], ws_mod["size"]), ("workshop", "123456", 777))

    def test_workshop_package_name(self):
        self.assertEqual(app.workshop_package_name("123456"), "mod_workshop_package.000000000001E240")


class ClassifyModTests(unittest.TestCase):
    def test_manifest_first(self):
        self.assertEqual(app.classify_mod(["map"], ["UI"], "Sound pack"), ("map", "manifest"))

    def test_paint_job_beats_truck(self):
        self.assertEqual(app.classify_mod(["truck", "paint_job"], [], ""), ("paint_job", "manifest"))

    def test_workshop_tags_before_generic_other(self):
        self.assertEqual(app.classify_mod(["other"], ["Sounds"], ""), ("sound", "workshop"))
        self.assertEqual(app.classify_mod(["other"], [], "Realistic physics"), ("other", "manifest"))

    def test_name_rules(self):
        self.assertEqual(app.classify_mod([], [], "ProMods Europe"), ("map", "name"))
        self.assertEqual(app.classify_mod([], [], "Скін пак DAF"), ("paint_job", "name"))
        self.assertEqual(app.classify_mod([], [], "Real Traffic Density"), ("ai_traffic", "name"))
        self.assertEqual(app.classify_mod([], [], "Ukrainian GPS voice"), ("ui", "name"))

    def test_unknown(self):
        self.assertEqual(app.classify_mod([], ["Weird"], "zzz"), ("other", "workshop"))
        self.assertEqual(app.classify_mod(["bogus"], [], "zzz"), (None, None))
        self.assertEqual(app.classify_mod([], [], ""), (None, None))


class PlanModSortTests(unittest.TestCase):
    def test_tiers_top_down(self):
        items = [{"name": "Map A", "cat": "map"}, {"name": "UI mod", "cat": "ui"},
                 {"name": "Sound X", "cat": "sound"}]
        plan = app.plan_mod_sort(items)
        self.assertEqual(plan["order"], [1, 2, 0])
        self.assertEqual(plan["families"], [])

    def test_stable_within_tier_and_unknown_is_other(self):
        items = [{"name": "Zeta", "cat": None}, {"name": "Alpha", "cat": "other"}, {"name": "Beta"}]
        self.assertEqual(app.plan_mod_sort(items)["order"], [0, 1, 2])

    def test_family_kept_together_at_top_member_tier(self):
        items = [{"name": "MAN TGX 2020 Rework", "cat": "truck"}, {"name": "Weather Pack", "cat": "graphics"},
                 {"name": "[ETS2] MAN TGX 2020 Tuning Parts", "cat": "tuning_parts"}]
        plan = app.plan_mod_sort(items)
        self.assertEqual(plan["families"], [[0, 2]])
        self.assertEqual(plan["tier"][0], plan["tier"][2])
        self.assertEqual(plan["order"], [1, 0, 2])

    def test_family_majority_tier(self):
        items = [{"name": "Big Pack Trailer One", "cat": "trailer"},
                 {"name": "Big Pack Trailer Two", "cat": "cargo_pack"},
                 {"name": "Big Pack Trailer Skin", "cat": "paint_job"}]
        plan = app.plan_mod_sort(items)
        trailer_tier = app._MOD_CAT_TIER["trailer"]
        self.assertEqual(plan["tier"], [trailer_tier] * 3)

    def test_family_rule(self):
        self.assertTrue(app._same_mod_family(["mercedes", "actros", "mp4"], ["mercedes", "actros", "mp5"]))
        self.assertFalse(app._same_mod_family(["man", "tgx", "2020"], ["man", "tgx", "euro6"]))  # "man tgx" = 7
        self.assertFalse(app._same_mod_family(["daf", "xf"], ["daf", "xg"]))
        self.assertFalse(app._same_mod_family(["a", "b"], ["a", "b"]))   # 2 words but under 8 chars


class ReorderProfileModsTests(unittest.TestCase):
    def mods(self, raw: bytes):
        return app.parse_sii_unit(raw.decode())["active_mods"]

    def test_reorder(self):
        out = app.reorder_profile_mods(PROFILE_SII.encode(), [2, 0, 1])
        self.assertEqual(self.mods(out), ["mod_c|Mod C", "mod_a|Mod A", "mod_b|Mod B"])
        self.assertEqual(out.decode().replace("mod_c|Mod C", "").count("\n"), PROFILE_SII.count("\n"))

    def test_identity_is_byte_exact(self):
        self.assertEqual(app.reorder_profile_mods(PROFILE_SII.encode(), [0, 1, 2]), PROFILE_SII.encode())

    def test_crlf_preserved(self):
        raw = PROFILE_SII.replace("\n", "\r\n").encode()
        out = app.reorder_profile_mods(raw, [1, 2, 0])
        self.assertNotIn(b"\n", out.replace(b"\r\n", b""))
        self.assertEqual(self.mods(out), ["mod_b|Mod B", "mod_c|Mod C", "mod_a|Mod A"])

    def test_encrypted_input_written_as_text(self):
        out = app.reorder_profile_mods(encrypt_scsc(PROFILE_SII.encode()), [2, 1, 0])
        self.assertTrue(out.startswith(b"SiiNunit"))
        self.assertEqual(self.mods(out)[0], "mod_c|Mod C")

    def test_errors(self):
        raw = PROFILE_SII.encode()
        with self.assertRaises(ValueError):
            app.reorder_profile_mods(raw, [0, 0, 1])
        with self.assertRaises(ValueError):
            app.reorder_profile_mods(raw, [0, 1])
        with self.assertRaises(ValueError):
            app.reorder_profile_mods(b"BSII\x03\0\0\0", [])
        with self.assertRaises(ValueError):
            app.reorder_profile_mods(raw.replace(b"active_mods: 3", b"active_mods: 4"), [0, 1, 2, 3])
        with self.assertRaises(ValueError):
            app.reorder_profile_mods(raw.replace(b" active_mods: 3\n", b""), [0, 1, 2])


class RemoveProfileModsTests(unittest.TestCase):
    def mods(self, raw: bytes):
        return app.sii_array(app.parse_sii_unit(raw.decode()), "active_mods")

    def test_remove_keeps_order_and_other_lines(self):
        out = app.remove_profile_mods(PROFILE_SII.encode(), {1})
        self.assertEqual(self.mods(out), ["mod_a|Mod A", "mod_c|Mod C"])
        text = out.decode()
        self.assertIn(" active_mods: 2\n active_mods[0]: \"mod_a|Mod A\"\n active_mods[1]: \"mod_c|Mod C\"\n", text)
        self.assertEqual(text.count("\n"), PROFILE_SII.count("\n") - 1)
        self.assertIn(' profile_name: "Test Driver"', text)

    def test_remove_all(self):
        out = app.remove_profile_mods(PROFILE_SII.encode(), [0, 1, 2])
        self.assertEqual(self.mods(out), [])
        self.assertIn(b" active_mods: 0\n profile_name", out)
        # Result is still a valid layout: reordering/removing again must not fail on it
        self.assertEqual(app.reorder_profile_mods(out, []), out)

    def test_crlf_and_encrypted(self):
        raw = encrypt_scsc(PROFILE_SII.replace("\n", "\r\n").encode())
        out = app.remove_profile_mods(raw, {0, 2})
        self.assertTrue(out.startswith(b"SiiNunit"))
        self.assertNotIn(b"\n", out.replace(b"\r\n", b""))
        self.assertEqual(self.mods(out), ["mod_b|Mod B"])

    def test_errors(self):
        raw = PROFILE_SII.encode()
        for bad in (set(), {3}, {-1}):
            with self.assertRaises(ValueError, msg=bad):
                app.remove_profile_mods(raw, bad)
        with self.assertRaises(ValueError):
            app.remove_profile_mods(b"BSII\x03\0\0\0", {0})


class SiiArrayTests(unittest.TestCase):
    def test_empty_array_is_count_only(self):
        data = app.parse_sii_unit("SiiNunit\n{\nuser_profile : x {\n active_mods: 0\n face: 1\n}\n}\n")
        self.assertEqual(data["active_mods"], "0")          # raw parser keeps the counter
        self.assertEqual(app.sii_array(data, "active_mods"), [])
        self.assertEqual(app.sii_array(data, "nothing"), [])
        self.assertEqual(app.sii_array(None, "active_mods"), [])
        self.assertEqual(app.sii_array(app.parse_sii_unit(PROFILE_SII), "active_mods")[0], "mod_a|Mod A")


if __name__ == "__main__":
    unittest.main()
