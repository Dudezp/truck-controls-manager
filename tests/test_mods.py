"""Mod manifests, installed-mod scan, classification, auto-sort plan and profile.sii reordering."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from helpers import app, encrypt_scsc, PROFILE_SII, MANIFEST_SII


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


if __name__ == "__main__":
    unittest.main()
