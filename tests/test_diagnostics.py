"""game.log.txt grouping, game.crash.txt parsing, crash suspects, net.log, search links."""
import unittest

from helpers import app

LOG_LINES = [
    "00:00:01.000 : <ERROR> [sound] File '/mod/horns.scs' not found",
    "00:00:02.000 : <WARNING> Bad data in workshop/content/227300/123456/def.scs",
    "00:00:02.500 : <WARNING> Package mod_workshop_package.000000000001E240 is outdated",
    "00:00:03.000 : <ERROR> File 'C:/x/mod/skins.zip':",
    "00:00:03.100 : <ERROR> Invalid texture",
    "00:00:04.000 : <WARNING> [map] Missing prefab 'prefab/x.ppd'",
    "00:00:05.000 : <WARNING> [map] Missing prefab 'prefab/x.ppd'",
    "00:00:06.000 : [sys] not an error line",
]


class LogGroupingTests(unittest.TestCase):
    def test_groups(self):
        groups = app.group_log_problems(LOG_LINES, {"123456": "Cool Mod"})
        summary = [(g["kind"], g["title"], g["errors"], g["warnings"]) for g in groups]
        self.assertEqual(summary, [
            ("mod", "skins.zip", 2, 0),
            ("mod", "horns.scs", 1, 0),
            ("mod", "Cool Mod", 0, 2),
            ("game", "[map] prefab/x.ppd", 0, 2),
        ])

    def test_unknown_workshop_name(self):
        (g,) = app.group_log_problems([LOG_LINES[1]], {})
        self.assertEqual(g["title"], "Workshop #123456")

    def test_group_key_masks_numbers(self):
        self.assertEqual(app._log_group_key("Timeout 1234 ms at 0x12"), app._log_group_key("Timeout 99 ms at 0x7"))


CRASH = """Crash log created on: Tue Nov 17 21:03:11 2026
Build: 1.56.1.0s (rev abc) win_x64
GPU: NVIDIA GeForce RTX 3060
OS: Windows 11
Exception code: C0000005
Fault address: 00007FF8A1B2C3D4 01:0000000000012345 C:\\Program Files\\RivaTuner\\RTSSHooks64.dll
Call stack (RTL):
00007FF8A1B2C3D4 RTSSHooks64.dll C:\\Program Files\\RivaTuner\\RTSSHooks64.dll
00007FF600001000 eurotrucks2.exe D:\\Steam\\steamapps\\common\\Euro Truck Simulator 2\\bin\\win_x64\\eurotrucks2.exe
00007FF8B0000000 ntdll.dll C:\\Windows\\System32\\ntdll.dll

Modules:
00007FF600000000 00100000 1.0 D:\\Steam\\steamapps\\common\\Euro Truck Simulator 2\\bin\\win_x64\\eurotrucks2.exe
00007FF8A1000000 00100000 7.3 C:\\Program Files\\RivaTuner\\RTSSHooks64.dll
00007FF8C0000000 00100000 1.0 D:\\Steam\\steamapps\\common\\Euro Truck Simulator 2\\bin\\win_x64\\dxgi.dll
00007FF8B0000000 00100000 10.0 C:\\Windows\\System32\\ntdll.dll

AvailMEM Virt:123 Phys:4096
"""


class CrashLogTests(unittest.TestCase):
    def test_parse(self):
        second = CRASH.replace("Tue Nov 17", "Wed Nov 18").replace("C0000005", "C00000FD")
        crashes = app.parse_crash_log("garbage before\n" + CRASH + "\n" + second)
        self.assertEqual(len(crashes), 2)
        c = crashes[0]
        self.assertEqual(c["when"].strftime("%Y-%m-%d %H:%M"), "2026-11-17 21:03")
        self.assertEqual((c["build"], c["exception"], c["fault_module"]), ("1.56.1.0s", "C0000005", "RTSSHooks64.dll"))
        self.assertEqual(c["third_party"], ["RTSSHooks64.dll"])
        self.assertEqual(len(c["modules"]), 3)
        self.assertEqual(c["avail_phys"], 4096)
        self.assertEqual(crashes[1]["exception"], "C00000FD")

    def test_empty(self):
        self.assertEqual(app.parse_crash_log(""), [])

    def test_classify_module(self):
        game_bin = "D:\\Games\\ETS2\\bin\\win_x64\\"
        self.assertEqual(app.classify_crash_module("C:\\RTSS\\RTSSHooks64.dll"), "rtss")
        self.assertEqual(app.classify_crash_module("C:/x/DiscordHook64.dll"), "discord")
        self.assertEqual(app.classify_crash_module(game_bin + "dxgi.dll"), "injector")
        self.assertEqual(app.classify_crash_module(game_bin + "mymod.asi"), "injector")
        self.assertIsNone(app.classify_crash_module(game_bin + "fmod.dll"))
        self.assertIsNone(app.classify_crash_module("C:\\Other\\dxgi.dll"))

    def test_suspects_stack_first(self):
        crashes = app.parse_crash_log(CRASH + "\n" + CRASH)
        sus = app.crash_suspects(crashes)
        self.assertEqual(sus, [("rtss", "RTSSHooks64.dll", 2, True), ("injector", "dxgi.dll", 2, False)])


NET_LOG = """17.11.2026 21:00:00 CORE INF: Started
17.11.2026 21:00:05 NET ERR: getaddinfo('eut2.online.scssoft.com') failed
17.11.2026 21:00:06 NET ERR: connect('1.2.3.4') failed 10060
17.11.2026 21:00:07 LINK ERR: Access denied.
17.11.2026 21:00:08 NET ERR: Error reading from socket
17.11.2026 21:01:00 CORE INF: Started
noise line
"""


class NetLogTests(unittest.TestCase):
    def test_parse(self):
        r = app.parse_net_log(NET_LOG)
        self.assertEqual((r["sessions"], r["errors"]), (2, 4))
        kinds = {g["kind"]: g for g in r["groups"]}
        self.assertEqual(set(kinds), {"net_dns", "net_timeout", "wot_denied", "net_reset"})
        self.assertEqual(kinds["net_dns"]["hosts"], {"eut2.online.scssoft.com"})
        self.assertEqual(kinds["net_timeout"]["hosts"], {"1.2.3.4"})

    def test_web_search_url(self):
        self.assertEqual(app.web_search_url("ets2 crash"), "https://www.google.com/search?q=ets2+crash")
        self.assertEqual(app.web_search_url("C0000005", "forum.scssoft.com"),
                         "https://www.google.com/search?q=site%3Aforum.scssoft.com+C0000005")


if __name__ == "__main__":
    unittest.main()
