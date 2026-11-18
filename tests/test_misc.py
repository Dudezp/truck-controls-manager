"""Steam VDF parsing, translations completeness, FAQ wiring, constants."""
import re
import string
import unittest

from helpers import app

VDF = r'''"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"apps"
		{
			"227300"		"123"
		}
	}
	"1"
	{
		"Path"		"E:\\SteamLibrary"
		"label"		"say \"hi\""
	}
}'''


class VdfTests(unittest.TestCase):
    def test_parse(self):
        d = app._parse_vdf(VDF)
        self.assertEqual(d["libraryfolders"]["0"]["path"], "C:\\Program Files (x86)\\Steam")
        self.assertEqual(d["libraryfolders"]["0"]["apps"], {"227300": "123"})
        self.assertEqual(d["libraryfolders"]["1"]["label"], 'say "hi"')

    def test_case_insensitive_get(self):
        d = app._parse_vdf(VDF)
        self.assertEqual(app._vdf_get_ci(d, "LibraryFolders", "1", "PATH"), "E:\\SteamLibrary")
        self.assertIsNone(app._vdf_get_ci(d, "libraryfolders", "9", "path"))
        self.assertIsNone(app._vdf_get_ci(d, "libraryfolders", "0", "path", "deeper"))


def placeholders(s: str) -> list:
    return sorted(f[1] or "" for f in string.Formatter().parse(s) if f[1] is not None)


class TranslationTests(unittest.TestCase):
    def test_same_keys(self):
        uk, en = app.LANGUAGES["uk"], app.LANGUAGES["en"]
        self.assertEqual(sorted(set(uk) - set(en)), [])
        self.assertEqual(sorted(set(en) - set(uk)), [])

    def test_same_placeholders(self):
        uk, en = app.LANGUAGES["uk"], app.LANGUAGES["en"]
        for k in uk:
            if isinstance(uk[k], str) and isinstance(en[k], str):
                self.assertEqual(placeholders(uk[k]), placeholders(en[k]), k)

    def test_faq_and_tabs_translated(self):
        for lang in ("uk", "en"):
            t = app.LANGUAGES[lang]
            for n, tab in app.FAQ_ITEMS:
                self.assertTrue(t.get(f"faq_q{n}"), f"{lang} faq_q{n}")
                self.assertTrue(t.get(f"faq_a{n}"), f"{lang} faq_a{n}")
                if tab:
                    self.assertIn(tab, app.TAB_KEYS)
            for tab in app.TAB_KEYS:
                self.assertIn(tab, t)

    def test_faq_numbers_unique(self):
        nums = [n for n, _ in app.FAQ_ITEMS]
        self.assertEqual(nums, sorted(set(nums)))

    def test_all_t_calls_have_keys(self):
        src = (app.Path(app.__file__)).read_text(encoding="utf-8")
        # Keys ending in "_" are prefixes completed at runtime (t('km_' + key)); checked below
        used = {k for k in re.findall(r"\bt\(\s*['\"]([a-z0-9_]+)['\"]", src) if not k.endswith("_")}
        missing = sorted(k for k in used if k not in app.LANGUAGES["uk"])
        self.assertEqual(missing, [])

    def test_dynamic_keys(self):
        keys = ["editor_field_" + f for f in app.SAVE_EDIT_FIELDS]
        keys += ["modsort_tier_" + k for k, _ in app.MOD_SORT_TIERS]
        keys += ["km_" + k for k, _ in app.KNOWN_CRASH_MODULES] + ["km_injector"]
        keys += ["km_" + k + "_tip" for k, _ in app.KNOWN_CRASH_MODULES] + ["km_injector_tip"]
        keys += ["mods_fmt_" + k for k in app.MOD_ARCHIVE_KINDS]
        for lang in ("uk", "en"):
            missing = [k for k in keys if k not in app.LANGUAGES[lang]]
            self.assertEqual(missing, [], lang)
            self.assertTrue(any(k.startswith("path_src_") for k in app.LANGUAGES[lang]))


class ConstantsTests(unittest.TestCase):
    def test_version_and_repo(self):
        self.assertRegex(app.APP_VERSION, r"^\d+\.\d+(\.\d+)?$")
        self.assertEqual(app.GITHUB_REPO, "Dudezp/truck-controls-manager")
        self.assertTrue(app._UPDATE_DOWNLOAD_PREFIX.startswith("https://github.com/Dudezp/"))

    def test_save_fields_types_are_scalars(self):
        for field, (_unit, t, lo, hi) in app.SAVE_EDIT_FIELDS.items():
            self.assertIn(t, app.BSIIReader.SCALARS, field)
            self.assertLess(lo, hi)


if __name__ == "__main__":
    unittest.main()
