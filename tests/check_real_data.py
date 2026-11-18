"""READ-ONLY check: runs app.py parsers over the real ETS2/ATS files listed in settings.json.
Use after a game update: python tests/check_real_data.py. Never writes into game folders."""
import re
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A  # noqa: E402

settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))


def ts(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def section(t):
    print(f"\n===== {t} =====")


def guard(label, fn):
    try:
        return fn()
    except Exception as e:
        print(f"  !! {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)
        return None


for game, appid in A.STEAM_APPS.items():
    section(game)
    home = Path(settings["game_paths"][game])
    print("home:", home, "exists:", home.exists())
    # Installed build (Steam)
    for lib in A.find_steam_libraries():
        man = lib / "steamapps" / f"appmanifest_{appid}.acf"
        if man.exists():
            d = A._parse_vdf(man.read_text(encoding="utf-8", errors="ignore"))
            st = A._vdf_get_ci(d, "AppState") or {}
            print("steam manifest:", man, "| buildid", st.get("buildid"), "| LastUpdated",
                  datetime.fromtimestamp(int(st.get("LastUpdated", 0))).strftime("%Y-%m-%d %H:%M"),
                  "| StateFlags", st.get("StateFlags"))
            inst = lib / "steamapps" / "common" / st.get("installdir", "")
            exe = inst / "bin" / "win_x64" / A.GAME_EXES[game]
            print("exe:", exe, "exists:", exe.exists(), "mtime:", ts(exe))
            dlcs = sorted(p.name for p in inst.glob("dlc_*.scs"))
            print(f"dlc files: {len(dlcs)}; newest:",
                  sorted(inst.glob("dlc_*.scs"), key=lambda p: p.stat().st_mtime)[-3:] and
                  [f"{p.name} {ts(p)}" for p in sorted(inst.glob("dlc_*.scs"), key=lambda p: p.stat().st_mtime)[-5:]])
            print("base scs:", [f"{p.name} {ts(p)}" for p in sorted(inst.glob("*.scs")) if not p.name.startswith("dlc_")])
    log = home / "game.log.txt"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
        ver = next((re.search(r"init ver\.([\w.]+)", l) for l in lines if "init ver." in l), None)
        print("game.log:", ts(log), "| version:", ver and ver.group(1), "| lines", len(lines))
        print("  head:", [l for l in lines[:12]])
        g = A.group_log_problems(lines, {})
        print("  log groups:", len(g), "errors", sum(x["errors"] for x in g), "warnings", sum(x["warnings"] for x in g))
        unknown = [l for l in lines if ("<ERROR>" in l or "<WARNING>" in l) and not A._LOG_LINE_RE.match(l)]
        print("  ERROR/WARNING lines not matched by regex:", len(unknown), unknown[:3])
    crash = home / "game.crash.txt"
    if crash.exists():
        cr = guard("crash", lambda: A.parse_crash_log(crash.read_text(encoding="utf-8", errors="ignore")))
        if cr:
            print("crash log:", ts(crash), "crashes", len(cr), "last build", cr[-1]["build"], cr[-1]["when"])
    net = home / "net.log"
    if net.exists():
        r = guard("net", lambda: A.parse_net_log(net.read_text(encoding="utf-8", errors="ignore")))
        if r:
            print("net.log:", ts(net), r["sessions"], "sessions", r["errors"], "errors",
                  [g["kind"] for g in r["groups"]])
    # global config.cfg cvars
    cfg = home / "config.cfg"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        print("config.cfg:", ts(cfg), "uset lines", len(re.findall(r"^uset ", text, re.M)))
    # profiles
    for pdir_name in ("steam_profiles", "profiles"):
        pdir = home / pdir_name
        if not pdir.is_dir():
            continue
        for prof in sorted(pdir.iterdir()):
            if not prof.is_dir():
                continue
            cloud = A.find_steam_cloud_profile_dir(game, prof.name)
            src = cloud if cloud and (cloud / "profile.sii").exists() else prof
            print(f"\n  profile {pdir_name}/{prof.name} cloud={bool(cloud)} name={A.read_profile_name(prof, cloud)!r}")
            psii = src / "profile.sii"
            raw = psii.read_bytes() if psii.exists() else b""
            print("   profile.sii:", ts(psii), "magic", raw[:4])
            data = A.read_sii_file(psii)
            if data is None:
                print("   !! profile.sii not parsed (binary?)", raw[:8])
            else:
                mods = A.sii_array(data, "active_mods")
                print(f"   keys {len(data)}; active_mods {len(mods)}; brand {data.get('brand')}; "
                      f"version {data.get('version')}; online {data.get('online_user_name', '')!r}")
                guard("layout", lambda: A._active_mods_layout(raw))
            for f in ("controls.sii", "config.cfg", "config_local.cfg"):
                for base in (prof, src):
                    if (base / f).exists():
                        print(f"   {f}: {ts(base / f)} magic {(base / f).read_bytes()[:8]}")
                        break
            ctr = prof / "controls.sii"
            if ctr.exists():
                c = A.read_sii_file(ctr)
                print("   controls.sii parsed:", c is not None, "keys", len(c or {}))
            # newest save
            saves = src / "save"
            if saves.is_dir():
                slots = sorted((d for d in saves.iterdir() if (d / "game.sii").exists()),
                               key=lambda d: (d / "game.sii").stat().st_mtime, reverse=True)
                print("   save slots:", len(slots))
                for slot in slots[:2]:
                    gs = slot / "game.sii"
                    def chk():
                        d = A.load_save_data(gs)
                        f = A.read_save_fields(d)
                        print(f"   save {slot.name} {ts(gs)} BSII v{A.BSIIReader(d).version}:",
                              {k: v[0] for k, v in f.items()})
                    guard(f"save {slot.name}", chk)
                    info = slot / "info.sii"
                    if info.exists():
                        i = A.read_sii_file(info)
                        print("    info.sii parsed:", i is not None, (i or {}).get("version"),
                              len(A.sii_array(i, "dependencies")), "deps")
    radio = home / "live_streams.sii"
    if radio.exists():
        r = guard("radio", lambda: A.read_live_streams(radio))
        if r:
            print("\nlive_streams.sii:", ts(radio), "stations", len(r[1]))
    srv = home / "server_config.sii"
    if srv.exists():
        v = guard("server", lambda: A.read_server_config(srv))
        print("server_config:", ts(srv), "fields", len(v or {}), "errors", A.validate_server_config(v or {}))
    # mods
    mods = guard("scan mods", lambda: A.scan_installed_mods(game, home))
    if mods is not None:
        ver = ""
        if log.exists():
            m = re.search(r"init ver\.([\w.]+)", log.read_text(encoding="utf-8", errors="ignore"))
            ver = m.group(1) if m else ""
        comp = {k: A.workshop_compatible(m["path"], ver) for k, m in mods.items() if m["source"] == "workshop"}
        print(f"\nmods: {len(mods)}; workshop compatible True/False/None:",
              sum(v is True for v in comp.values()), sum(v is False for v in comp.values()),
              sum(v is None for v in comp.values()))
