"""Self-update: version compare, GitHub release check, verified download, exe swap (network mocked)."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from helpers import app

ASSET_NAME = "TruckControlsManager_v9.9_Portable.zip"
ASSET_URL = app._UPDATE_DOWNLOAD_PREFIX + "v9.9/" + ASSET_NAME


def release(**kw):
    r = {"tag_name": "v9.9", "draft": False, "prerelease": False, "html_url": "https://github.com/x",
         "name": "v9.9", "body": "Notes",
         "assets": [{"name": ASSET_NAME, "size": 123, "browser_download_url": ASSET_URL,
                     "digest": "sha256:" + "ab" * 32}]}
    r.update(kw)
    return r


def serve(payload: bytes):
    return mock.patch.object(app.urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(payload))


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(app.parse_version("v3.0"), (3, 0))
        self.assertEqual(app.parse_version("1.2.3.4.5"), (1, 2, 3, 4))
        self.assertEqual(app.parse_version(""), (0,))

    def test_newer(self):
        self.assertTrue(app.is_newer_version("v3.1", "3.0"))
        self.assertTrue(app.is_newer_version("3.0.1", "3.0"))
        self.assertFalse(app.is_newer_version("3.0.0", "3.0"))
        self.assertFalse(app.is_newer_version("v2.9", "3.0"))
        self.assertTrue(app.is_newer_version("10.0", "9.9"))


class CheckForUpdateTests(unittest.TestCase):
    def check(self, rel, current="3.0"):
        with serve(json.dumps(rel).encode()):
            return app.check_for_update(current)

    def test_newer_release_with_digest(self):
        info = self.check(release())
        self.assertEqual((info["version"], info["asset_url"], info["size"], info["sha256"]),
                         ("9.9", ASSET_URL, 123, "ab" * 32))

    def test_not_newer_draft_prerelease(self):
        self.assertIsNone(self.check(release(tag_name="v3.0")))
        self.assertIsNone(self.check(release(draft=True)))
        self.assertIsNone(self.check(release(prerelease=True)))

    def test_sha_from_body_fallback(self):
        rel = release(body=f"SHA-256 of {ASSET_NAME}: `{'CD' * 32}`")
        del rel["assets"][0]["digest"]
        self.assertEqual(self.check(rel)["sha256"], "cd" * 32)

    def test_no_sha(self):
        rel = release()
        del rel["assets"][0]["digest"]
        self.assertIsNone(self.check(rel)["sha256"])

    def test_foreign_url_rejected(self):
        rel = release()
        rel["assets"][0]["browser_download_url"] = "https://evil.example/x.zip"
        self.assertIsNone(self.check(rel)["asset_url"])

    def test_no_portable_asset(self):
        rel = release(assets=[{"name": "source.tar.gz"}])
        info = self.check(rel)
        self.assertIsNone(info["asset_url"])
        self.assertIsNone(info["sha256"])


def make_zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class DownloadUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def download(self, payload, **kw):
        info = {"asset_url": ASSET_URL, "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
        info.update(kw)
        with serve(payload):
            return app.download_update(info, self.dir, **{k: v for k, v in kw.items() if k in ("progress", "cancel")})

    def test_ok(self):
        payload = make_zip_bytes({"TruckControlsManager.exe": b"MZ new", "README_UA.txt": "x"})
        seen = []
        info = {"asset_url": ASSET_URL, "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
        with serve(payload):
            exe = app.download_update(info, self.dir, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(exe.read_bytes(), b"MZ new")
        self.assertEqual(seen[-1], (len(payload), len(payload)))

    def test_sha_mismatch(self):
        payload = make_zip_bytes({"a.exe": b"MZ"})
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.download(payload, sha256="00" * 32)

    def test_size_mismatch(self):
        payload = make_zip_bytes({"a.exe": b"MZ"})
        with self.assertRaisesRegex(ValueError, "size"):
            self.download(payload, size=len(payload) + 1)

    def test_bad_archives(self):
        for files in ({"a.exe": b"MZ", "b.exe": b"MZ"}, {"sub/a.exe": b"MZ"}, {"a.exe": b"PK not exe"}):
            with self.assertRaises(ValueError, msg=str(files)):
                self.download(make_zip_bytes(files))

    def test_requires_url_and_sha(self):
        with self.assertRaises(ValueError):
            app.download_update({"asset_url": ASSET_URL, "sha256": None}, self.dir)
        with self.assertRaises(ValueError):
            app.download_update({"asset_url": None, "sha256": "ab" * 32}, self.dir)

    def test_cancel(self):
        payload = make_zip_bytes({"a.exe": b"MZ"})
        with self.assertRaises(InterruptedError):
            self.download(payload, cancel=lambda: True)


class InstallUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cur, self.new = d / "Tool.exe", d / "Tool.new.exe"
        self.cur.write_bytes(b"old")
        self.new.write_bytes(b"new")

    def tearDown(self):
        self.tmp.cleanup()

    def test_swap_and_backup(self):
        prev = self.cur.with_name("Tool_previous.exe")
        prev.write_bytes(b"older")
        backup = app.install_update(self.new, self.cur)
        self.assertEqual(backup, prev)
        self.assertEqual((self.cur.read_bytes(), prev.read_bytes()), (b"new", b"old"))

    def test_rollback_on_copy_failure(self):
        with mock.patch.object(app.shutil, "copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                app.install_update(self.new, self.cur)
        self.assertEqual(self.cur.read_bytes(), b"old")
        self.assertFalse(self.cur.with_name("Tool_previous.exe").exists())

    def test_launch_detached_resets_pyinstaller_env(self):
        with mock.patch("subprocess.Popen") as popen:
            app.launch_detached(self.cur)
        self.assertEqual(popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.cur.parent))


if __name__ == "__main__":
    unittest.main()
