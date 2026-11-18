"""Internet radio: live_streams.sii, entries, Radio Browser catalogue filtering (network mocked)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import app


class RadioEntryTests(unittest.TestCase):
    def test_parse_pads_missing_fields(self):
        st = app.parse_radio_entry("http://x/stream|Name")
        self.assertEqual(st, {"url": "http://x/stream", "name": "Name", "genre": "", "lang": "",
                              "bitrate": "", "favorite": ""})

    def test_format_sanitizes_separators(self):
        st = {"url": "http://x", "name": "A|B\nC", "genre": "Rock", "lang": "UA", "bitrate": 128, "favorite": "0"}
        self.assertEqual(app.format_radio_entry(st), "http://x|A/B C|Rock|UA|128|0")
        self.assertEqual(app.parse_radio_entry(app.format_radio_entry(st))["name"], "A/B C")


class LiveStreamsFileTests(unittest.TestCase):
    def test_roundtrip_unicode(self):
        stations = [
            {"url": "http://a/mp3", "name": 'Радіо "Рокс"', "genre": "Рок", "lang": "UA", "bitrate": "128",
             "favorite": "1"},
            {"url": "https://b/aac", "name": "B FM", "genre": "Pop", "lang": "PL", "bitrate": "64",
             "favorite": "0"},
        ]
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "live_streams.sii"
            app.write_live_streams(p, "_nameless.abc", stations)
            raw = p.read_bytes()
            self.assertTrue(raw.isascii())
            self.assertIn(b"\r\n", raw)
            unit, back = app.read_live_streams(p)
        self.assertEqual(unit, "_nameless.abc")
        self.assertEqual(back, stations)

    def test_default_unit_name(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "live_streams.sii"
            p.write_text("SiiNunit\n{\n}\n")
            self.assertEqual(app.read_live_streams(p), ("_nameless.radio", []))


def catalogue(**kw):
    s = {"name": "Rock FM", "url": "http://r/x", "url_resolved": "http://r/stream.mp3", "codec": "MP3",
         "hls": 0, "tags": "rock,classic rock,pop", "countrycode": "ua", "bitrate": 192, "votes": 10,
         "stationuuid": "u1"}
    s.update(kw)
    return s


class RadioBrowserStationTests(unittest.TestCase):
    def test_good_station(self):
        st = app.radio_browser_station(catalogue())
        self.assertEqual((st["url"], st["genre"], st["lang"], st["bitrate"]),
                         ("http://r/stream.mp3", "Rock, Classic rock", "UA", "192"))

    def test_defaults(self):
        st = app.radio_browser_station(catalogue(tags="", countrycode="", bitrate=0, codec="aac"))
        self.assertEqual((st["genre"], st["lang"], st["bitrate"], st["codec"]), ("Radio", "INT", "128", "AAC"))

    def test_rejected(self):
        for kw in ({"codec": "OGG"}, {"hls": 1}, {"url_resolved": "http://r/list.m3u8"},
                   {"url_resolved": "http://r/a.pls?x=1"}, {"url_resolved": "ftp://r/x"},
                   {"url_resolved": "http://r/a|b"}, {"name": "   "}):
            self.assertIsNone(app.radio_browser_station(catalogue(**kw)), kw)


class SearchRadioBrowserTests(unittest.TestCase):
    def test_dedup_filter_and_failover(self):
        data = [catalogue(), catalogue(name="Dup"), catalogue(codec="FLAC"),
                catalogue(name="Two", url_resolved="http://r/2.mp3")]
        calls = []

        def fake_urlopen(req, timeout=0):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise OSError("first host down")
            return io.BytesIO(json.dumps(data).encode())

        with mock.patch.object(app.urllib.request, "urlopen", fake_urlopen):
            res = app.search_radio_browser(name="rock", country="UA", limit=5)
        self.assertEqual([s["name"] for s in res], ["Rock FM", "Two"])
        self.assertEqual(len(calls), 2)
        self.assertIn("countrycode=UA", calls[1])

    def test_all_hosts_down(self):
        with mock.patch.object(app.urllib.request, "urlopen", side_effect=OSError("down")):
            with self.assertRaises(ConnectionError):
                app.search_radio_browser(tag="rock")


if __name__ == "__main__":
    unittest.main()
