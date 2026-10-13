"""
Truck Controls Manager (ETS2 & ATS) v3.0
Повний центр керування налаштуваннями, пресетами, сейвами та оптимізацією.
"""

import os
import sys
import re
import json
import shutil
import zlib
import time
import struct
import zipfile
import urllib.request
import urllib.parse
import threading
import logging
import ctypes
from ctypes import wintypes
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
import customtkinter as ctk
from PIL import Image

# Налаштування теми
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Tk не потокобезпечний. Якщо збирач сміття спрацює у фоновому потоці, __del__ шрифтів, змінних і
# зображень Tk звернеться до Tcl не з головного потоку — у Python 3.14 це блокує обидва потоки
# (вікно зависає). Такі звільнення відкладаються і виконуються в головному потоці.
# Без блокувань: list.append атомарний, а блокування всередині збирача сміття саме могло б зависнути.
_deferred_tk_deletes = []

def _make_main_thread_del(cls):
    orig = cls.__del__
    def __del__(self):
        if threading.current_thread() is threading.main_thread():
            orig(self)
        else:
            _deferred_tk_deletes.append((orig, self))
    cls.__del__ = __del__

def _install_tk_del_guards():
    import tkinter.font as tkfont
    classes = [tkfont.Font, tk.Variable, tk.Image]
    try:
        from PIL import ImageTk
        classes.append(ImageTk.PhotoImage)
    except Exception:
        pass
    for cls in classes:
        if '__del__' in vars(cls):
            _make_main_thread_del(cls)

_install_tk_del_guards()

def flush_deferred_tk_deletes():
    """Викликається з головного потоку: звільняє ресурси Tk, зібрані у фонових потоках."""
    items = _deferred_tk_deletes[:]
    del _deferred_tk_deletes[:len(items)]
    for orig, obj in items:
        try:
            orig(obj)
        except Exception:
            pass

# ============================================================================
# УКРАЇНСЬКА АКЦЕНТНА КОЛЬОРОВА ПАЛІТРА (Ukrainian Flag Theme)
# ============================================================================
UA_BLUE = "#0057B7"             # Офіційний синій прапора України (Pantone 2935 C)
UA_BLUE_HOVER = "#004899"       # Насичений синій при наведенні
UA_BLUE_LIGHT = "#389BFF"       # Яскравий волошковий для тексту/підсвітки
UA_YELLOW = "#FFD700"           # Сонячно-золотий жовтий прапора України
UA_YELLOW_HOVER = "#E6C200"     # Жовтий при наведенні
UA_YELLOW_LIGHT = "#FFE55C"     # Світло-жовтий для кнопок і перемикачів
UA_YELLOW_DARK = "#B89600"      # Темно-золотий для рамок
UA_YELLOW_TEXT_DARK = "#0F172A" # Контрастний темний текст на жовтому фоні

# Базові кольори інтерфейсу
COLOR_BG_DARK = "#141518"
COLOR_CARD_DARK = "#1E1F24"
COLOR_CARD_INNER = "#25272D"
COLOR_CARD_HOVER = "#2D3039"
COLOR_BORDER = "#374151"
COLOR_TEXT_WHITE = "#F9FAFB"
COLOR_TEXT_MUTED = "#9CA3AF"

# Статуси
COLOR_SUCCESS = "#10B981"
COLOR_SUCCESS_HOVER = "#059669"
COLOR_DANGER = "#EF4444"
COLOR_DANGER_HOVER = "#DC2626"

APP_NAME = "Truck Controls Manager"
APP_VERSION = "3.0"
CONFIG_FILE = "settings.json"
PRESETS_DIR_NAME = "Controls_Presets"
SAVES_BACKUP_DIR_NAME = "Save_Backups"
CONFIG_BACKUP_DIR_NAME = "Config_Backups"

GAMES = {
    "Euro Truck Simulator 2": "Euro Truck Simulator 2",
    "American Truck Simulator": "American Truck Simulator"
}

GAME_EXES = {
    "Euro Truck Simulator 2": "eurotrucks2.exe",
    "American Truck Simulator": "amtrucks.exe"
}

STEAM_APPS = {
    "Euro Truck Simulator 2": 227300,
    "American Truck Simulator": 270880
}

PROFILE_FILES = ("controls.sii", "config.cfg", "config_local.cfg")
GLOBAL_FILES = ("global_controls.sii",)
PRESET_MANIFEST = "tcm_preset.json"
TAB_KEYS = ("tab_controls", "tab_tweaks", "tab_saves", "tab_mods", "tab_radio", "tab_logs", "tab_clean_mods", "tab_settings")

# Часті питання: (номер у перекладах faq_qN/faq_aN, вкладка для кнопки переходу або None)
FAQ_ITEMS = (
    (1, None), (2, None), (3, "tab_tweaks"), (4, "tab_saves"), (5, "tab_controls"), (6, "tab_tweaks"),
    (7, "tab_saves"), (8, "tab_mods"), (9, "tab_mods"), (10, "tab_mods"), (11, "tab_radio"), (12, "tab_radio"),
    (13, "tab_logs"), (14, "tab_logs"), (15, "tab_clean_mods"), (16, None), (17, "tab_settings"),
)
PRESET_MAX_FILE_SIZE = 10 * 1024 * 1024
# У Steam Cloud профілів ці елементи лежать у Steam\userdata\...\remote, а не в steam_profiles
CLOUD_ITEMS = ("config.cfg", "profile.sii", "save")

# Змінні, які гра читає з профілю (за документацією SCS), а не з загального config.cfg.
# Значення — файл профілю, куди писати, якщо змінної ще немає в жодному з файлів профілю.
PROFILE_CVARS = {
    "g_police": "config.cfg",
    "g_fatigue": "config.cfg",
    "g_cam_physics": "config.cfg",
    "g_cam_physics_value": "config.cfg",
    "g_cabin_suspension_stiffness": "config.cfg",
    "g_truck_stability": "config.cfg",
    "g_trailer_stability": "config.cfg",
    "g_brake_intensity": "config_local.cfg",
    "r_steering_wheel": "config_local.cfg",
    "g_force_economy_reset": "config_local.cfg",
}

# SCS SII Розшифрування
SCSC_MAGIC = b'ScsC'
SCS_KEY = bytes.fromhex('2A5FCB1791D22FB60245B3D8369ED0B2C27371563FBF1F3C9EDF6B11825A5D0A')
SCSC_HEADER = 56

def _decrypt_scsc(data: bytes) -> bytes | None:
    try:
        from Crypto.Cipher import AES
        if data[:4] != SCSC_MAGIC or len(data) < SCSC_HEADER:
            return None
        iv = data[36:52]
        cipher = AES.new(SCS_KEY, AES.MODE_CBC, iv)
        plain = cipher.decrypt(data[SCSC_HEADER:])
        return zlib.decompress(plain)
    except Exception:
        return None

def _sii_to_text(raw: bytes) -> str:
    if raw[:4] == SCSC_MAGIC:
        dec = _decrypt_scsc(raw)
        if dec:
            raw = dec
    for enc in ('utf-8', 'latin-1', 'cp1251'):
        try:
            return raw.decode(enc, errors='ignore')
        except Exception:
            pass
    return ''

_SII_LINE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d*)\])?\s*:\s*(.*?)\s*$')

_SII_HEX_SEQ = re.compile(r'(?:\\x[0-9a-fA-F]{2})+')

def sii_unescape(val: str) -> str:
    r"""SII кодує не-ASCII символи як \xNN (байти UTF-8) і лапки як \"."""
    val = _SII_HEX_SEQ.sub(lambda m: bytes.fromhex(m.group(0).replace('\\x', '')).decode('utf-8', 'replace'), val)
    return val.replace('\\"', '"')

def parse_sii_unit(text: str) -> dict:
    """Читає поля першого юніта текстового SII. Масиви (key[i]) повертаються списками."""
    data, arrays = {}, {}
    depth = 0
    for line in text.splitlines():
        s = line.strip()
        if s.endswith('{'):
            depth += 1
            continue
        if s.startswith('}'):
            depth -= 1
            if depth <= 1 and (data or arrays):
                break
            continue
        if depth < 2:
            continue
        m = _SII_LINE.match(line)
        if not m:
            continue
        key, idx, val = m.groups()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = sii_unescape(val[1:-1])
        if idx is not None:
            arrays.setdefault(key, []).append(val)
        else:
            data[key] = val
    data.update(arrays)
    return data

def sii_float(val) -> float | None:
    """SII зберігає float або звичайним числом, або як &hex (IEEE 754 big-endian)."""
    try:
        if isinstance(val, str) and val.startswith('&'):
            return struct.unpack('>f', bytes.fromhex(val[1:]))[0]
        return float(val)
    except Exception:
        return None

def read_sii_file(path: Path) -> dict | None:
    """Розшифровує (за потреби) і парсить текстовий SII. None — файл відсутній або бінарний."""
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if raw[:4] == SCSC_MAGIC:
        raw = _decrypt_scsc(raw) or b''
    if not raw.lstrip(b'\xef\xbb\xbf').startswith(b'SiiNunit'):
        return None
    return parse_sii_unit(raw.decode('utf-8', errors='ignore'))

def decode_folder_hex_name(hex_str: str) -> str:
    try:
        decoded = bytes.fromhex(hex_str).decode('utf-8')
        if decoded and all(32 <= ord(c) < 127 or ord(c) > 160 for c in decoded):
            return decoded
    except Exception:
        pass
    return ''

def read_profile_name(profile_folder: Path, cloud_dir: Path | None = None) -> str:
    sii = profile_folder / 'profile.sii'
    if cloud_dir and (cloud_dir / 'profile.sii').exists():
        sii = cloud_dir / 'profile.sii'
    if sii.exists():
        try:
            text = _sii_to_text(sii.read_bytes())
            m = re.search(r'profile_name\s*:\s*"([^"]+)"', text)
            if m:
                return m.group(1).strip()
            m = re.search(r'profile_name\s*:\s*([^\s\n]+)', text)
            if m:
                name = m.group(1).strip().strip('"')
                if name and len(name) < 80 and not name.startswith('@'):
                    return name
        except Exception:
            pass

    hex_name = decode_folder_hex_name(profile_folder.name)
    if hex_name:
        return hex_name

    return profile_folder.name

# Визначення запущених процесів без сторонніх бібліотек через Win32 API
TH32CS_SNAPPROCESS = 0x00000002

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_char * 260)
    ]

def is_game_running(game_name: str) -> bool:
    """Перевіряє, чи запущений процес гри в Windows."""
    target_exe = GAME_EXES.get(game_name, "").lower()
    if not target_exe:
        return False
    try:
        hSnapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if hSnapshot == -1:
            return False
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        found = False
        if ctypes.windll.kernel32.Process32First(hSnapshot, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode('utf-8', errors='ignore').lower()
                if name == target_exe:
                    found = True
                    break
                if not ctypes.windll.kernel32.Process32Next(hSnapshot, ctypes.byref(pe)):
                    break
        ctypes.windll.kernel32.CloseHandle(hSnapshot)
        return found
    except Exception:
        return False

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT),
        ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR),
        ('fFlags', ctypes.c_uint16), ('fAnyOperationsAborted', wintypes.BOOL),
        ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR),
    ]

def send_to_recycle_bin(paths: list) -> bool:
    """Переміщує файли/папки в Кошик Windows (можна відновити)."""
    if not paths:
        return True
    joined = "\0".join(str(Path(p).resolve()) for p in paths) + "\0\0"
    buf = (ctypes.c_wchar * len(joined))(*joined)
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = _SHFILEOPSTRUCTW(None, FO_DELETE, ctypes.cast(buf, wintypes.LPCWSTR), None,
                          FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    try:
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:
        return False
    return rc == 0 and not op.fAnyOperationsAborted

def get_dir_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    except Exception:
        return 0

def fmt_size(total: int) -> str:
    if total > 1024 * 1024 * 1024:
        return f"{total / (1024 * 1024 * 1024):.2f} GB"
    if total > 1024 * 1024:
        return f"{total / (1024 * 1024):.1f} MB"
    return f"{total / 1024:.1f} KB"

_GAME_BACKUP_RE = re.compile(r'^(steam_profiles|profiles)\((.+)\)\.bak$', re.I)

def list_game_auto_backups(game_path: Path) -> list:
    """Бекапи профілів, які гра робить сама при оновленні: steam_profiles(1.60.1.7s).bak.
    Повертає від найновішої версії до найстарішої."""
    result = []
    try:
        for d in game_path.iterdir():
            m = _GAME_BACKUP_RE.match(d.name)
            if d.is_dir() and m:
                ver = tuple(int(x) for x in re.findall(r'\d+', m.group(2)))
                result.append({'path': d, 'version': m.group(2), 'key': (ver, d.stat().st_mtime)})
    except Exception:
        return []
    result.sort(key=lambda b: b['key'], reverse=True)
    return result

# ============================================================================
# МОДИ: локальна папка mod та Steam Workshop
# ============================================================================
def find_steam_libraries() -> list:
    steam = find_steam_dir()
    if not steam:
        return []
    libs = [steam]
    vdf = steam / 'steamapps' / 'libraryfolders.vdf'
    try:
        data = _vdf_get_ci(_parse_vdf(vdf.read_text(encoding='utf-8', errors='ignore')), 'libraryfolders') or {}
        for entry in data.values():
            if isinstance(entry, dict) and entry.get('path'):
                p = Path(entry['path'])
                if p not in libs:
                    libs.append(p)
    except Exception:
        pass
    return libs

def find_workshop_dir(game_name: str) -> Path | None:
    appid = str(STEAM_APPS.get(game_name, ''))
    for lib in find_steam_libraries():
        d = lib / 'steamapps' / 'workshop' / 'content' / appid
        if d.is_dir():
            return d
    return None

def read_workshop_sizes(workshop_dir: Path, game_name: str) -> dict:
    """Розміри предметів Workshop з appworkshop_<appid>.acf (без обходу диска)."""
    appid = str(STEAM_APPS.get(game_name, ''))
    acf = workshop_dir.parent.parent / f'appworkshop_{appid}.acf'
    try:
        items = _vdf_get_ci(_parse_vdf(acf.read_text(encoding='utf-8', errors='ignore')),
                            'AppWorkshop', 'WorkshopItemsInstalled') or {}
        return {k: int(v.get('size', 0)) for k, v in items.items() if isinstance(v, dict)}
    except Exception:
        return {}

def _manifest_display_name(raw: bytes) -> str | None:
    if raw[:4] == SCSC_MAGIC:
        raw = _decrypt_scsc(raw) or b''
    name = parse_sii_unit(raw.decode('utf-8', errors='ignore')).get('display_name')
    if name and not name.startswith('@@'):
        return name.strip()
    return None

def read_mod_display_name(path: Path) -> str | None:
    """display_name з manifest.sii мода: папка, .zip/.scs (ZIP) або папка Workshop з версіями."""
    try:
        if path.is_file():
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    if 'manifest.sii' in zf.namelist():
                        return _manifest_display_name(zf.read('manifest.sii'))
            return None
        if (path / 'manifest.sii').is_file():
            return _manifest_display_name((path / 'manifest.sii').read_bytes())
        # Workshop: latest / NNN_content / universal та архіви в них — найновіші першими
        def rank(p):
            m = re.match(r'(\d+)_content', p.stem)
            return (p.stem == 'latest', int(m.group(1)) if m else -1, p.stem == 'universal')
        for sub in sorted(path.iterdir(), key=rank, reverse=True):
            if sub.suffix.lower() in ('.zip', '.scs') or sub.is_dir():
                name = read_mod_display_name(sub) if sub.is_file() or (sub / 'manifest.sii').is_file() else None
                if name:
                    return name
    except Exception:
        pass
    return None

def read_mod_categories(path: Path) -> list:
    """category[] з manifest.sii мода (ті самі місця пошуку, що й read_mod_display_name).
    Архіви HashFS (.scs не у форматі ZIP) без розпакування не читаються — тоді []."""
    def from_raw(raw):
        if raw[:4] == SCSC_MAGIC:
            raw = _decrypt_scsc(raw) or b''
        cats = parse_sii_unit(raw.decode('utf-8', errors='ignore')).get('category') or []
        return [c.strip().lower() for c in (cats if isinstance(cats, list) else [cats]) if c.strip()]
    try:
        if path.is_file():
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    if 'manifest.sii' in zf.namelist():
                        return from_raw(zf.read('manifest.sii'))
            return []
        if (path / 'manifest.sii').is_file():
            return from_raw((path / 'manifest.sii').read_bytes())
        def rank(p):
            m = re.match(r'(\d+)_content', p.stem)
            return (p.stem == 'latest', int(m.group(1)) if m else -1, p.stem == 'universal')
        for sub in sorted(path.iterdir(), key=rank, reverse=True):
            if sub.suffix.lower() in ('.zip', '.scs') or (sub.is_dir() and (sub / 'manifest.sii').is_file()):
                cats = read_mod_categories(sub)
                if cats:
                    return cats
    except Exception:
        pass
    return []

def workshop_compatible(path: Path, game_version: str) -> bool | None:
    """Чи має мод Workshop версію для поточної гри (versions.sii). None — невідомо."""
    vs = path / 'versions.sii'
    if not game_version or not vs.is_file():
        return None
    try:
        text = vs.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return None
    units = re.split(r'package_version_info\s*:', text)[1:]
    if not units:
        return None
    import fnmatch
    for unit in units:
        patterns = re.findall(r'compatible_versions\[\d*\]\s*:\s*"([^"]+)"', unit)
        if not patterns or any(fnmatch.fnmatch(game_version, p) for p in patterns):
            return True
    return False

def _workshop_cache_file() -> Path:
    base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'TruckControlsManager'
    return base / 'workshop_names.json'

def load_workshop_title_cache() -> dict:
    try:
        return json.loads(_workshop_cache_file().read_text(encoding='utf-8'))
    except Exception:
        return {}

def save_workshop_title_cache(cache: dict):
    try:
        f = _workshop_cache_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass

def fetch_workshop_titles(ids: list) -> dict:
    """Назви предметів Workshop через публічний Steam Web API (без ключа)."""
    return {k: v['title'] for k, v in fetch_workshop_details(ids).items() if v['title']}

def _workshop_tags_file() -> Path:
    return _workshop_cache_file().parent / "workshop_tags.json"

def get_workshop_tags(ids: list) -> dict:
    """{id: [теги]} предметів Workshop. Теги майже не змінюються, тому кешуються на диску."""
    try:
        cache = json.loads(_workshop_tags_file().read_text(encoding='utf-8'))
    except Exception:
        cache = {}
    missing = [i for i in dict.fromkeys(ids) if i not in cache]
    if missing:
        fetched = fetch_workshop_details(missing)
        if fetched:
            cache.update({k: v['tags'] for k, v in fetched.items()})
            try:
                _workshop_tags_file().parent.mkdir(parents=True, exist_ok=True)
                _workshop_tags_file().write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
    return {i: cache.get(i, []) for i in ids}

def fetch_workshop_details(ids: list) -> dict:
    """{id: {'title', 'tags'}} через публічний Steam Web API (без ключа)."""
    details = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        form = {"itemcount": str(len(chunk))}
        form.update({f"publishedfileids[{n}]": wid for n, wid in enumerate(chunk)})
        req = urllib.request.Request(
            "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
            data=urllib.parse.urlencode(form).encode(),
            headers={'User-Agent': f'TruckControlsManager/{APP_VERSION}'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='ignore'))
            for item in data.get('response', {}).get('publishedfiledetails', []):
                if item.get('result') == 1:
                    details[str(item['publishedfileid'])] = {
                        'title': item.get('title') or "",
                        'tags': [t.get('tag', "") for t in item.get('tags', []) if t.get('tag')]}
        except Exception:
            break
    return details

def workshop_package_name(ws_id: str) -> str:
    return f"mod_workshop_package.{int(ws_id):016X}"

def scan_installed_mods(game_name: str, game_path: Path) -> dict:
    """{пакет: {'pkg','name','source','path','size','ws_id'}} для папки mod та Workshop."""
    mods = {}
    mod_dir = game_path / 'mod'
    if mod_dir.is_dir():
        for f in mod_dir.iterdir():
            if f.is_dir() or f.suffix.lower() in ('.scs', '.zip'):
                mods[f.stem if f.is_file() else f.name] = {
                    'pkg': f.stem if f.is_file() else f.name, 'name': read_mod_display_name(f),
                    'source': 'local', 'path': f, 'size': get_dir_size(f), 'ws_id': None}
    ws_dir = find_workshop_dir(game_name)
    if ws_dir:
        sizes = read_workshop_sizes(ws_dir, game_name)
        for d in ws_dir.iterdir():
            if d.is_dir() and d.name.isdigit():
                pkg = workshop_package_name(d.name)
                mods[pkg] = {'pkg': pkg, 'name': read_mod_display_name(d), 'source': 'workshop',
                             'path': d, 'size': sizes.get(d.name) or get_dir_size(d), 'ws_id': d.name}
    return mods

# ============================================================================
# АВТОСОРТУВАННЯ МОДІВ
# У profile.sii active_mods[0] — НАЙНИЖЧИЙ пріоритет; у менеджері модів гри згори показано найвищий.
# Правила (згори вниз): те, що має перекривати інших, — вище; карти й залежності — в самому низу.
# ============================================================================
MOD_SORT_TIERS = (
    ("ui", ("ui",)),
    ("graphics", ("graphics", "weather_setup")),
    ("sound", ("sound",)),
    ("physics", ("physics",)),
    ("other", ("other",)),
    ("paint", ("paint_job",)),
    ("parts", ("tuning_parts", "interior")),
    ("truck", ("truck",)),
    ("trailer", ("trailer", "cargo_pack")),
    ("traffic", ("ai_traffic",)),
    ("models", ("models", "prefabs", "movers", "walkers")),
    ("map", ("map",)),
)
_MOD_CAT_TIER = {c: i for i, (_k, cats) in enumerate(MOD_SORT_TIERS) for c in cats}

# Теги Steam Workshop -> категорія manifest.sii, у порядку переваги
_WS_TAG_CATS = (
    ("ui", "ui"), ("graphics", "graphics"), ("weather setups", "weather_setup"), ("sounds", "sound"),
    ("voice navigation", "sound"), ("physics", "physics"), ("paintjobs", "paint_job"), ("interiors", "interior"),
    ("maps", "map"), ("map", "map"), ("ai traffic", "ai_traffic"), ("trucks", "truck"), ("trailers", "trailer"),
    ("cargo packs", "cargo_pack"), ("truck parts", "tuning_parts"), ("cabin accessories", "tuning_parts"),
    ("models", "models"), ("other types", "other"),
)

# Остання підказка — назва мода (для архівів HashFS без доступного manifest.sii і без тегів)
_NAME_CAT_RULES = (
    ("map", r"\bmaps?\b|promods|\broex\b|карт[аи]\b"),
    ("ui", r"\bui\b|\bhud\b|navigat|навігат|\bgps\b|loading screen"),
    ("graphics", r"graphic|weather|reshade|\bsky|lighting|texture|графік|погод"),
    ("sound", r"sound|\bhorns?\b|voice|звук|голос"),
    ("physics", r"physic|фізик"),
    ("paint_job", r"\bskins?\b|skinpack|paint|livery|скін|розфарб"),
    ("tuning_parts", r"interior|dashboard|tuning|accessor|\bparts?\b|flare|light ?bars?|інтер.?єр|тюнінг|аксесуар"),
    ("ai_traffic", r"traffic|трафік|\bai\b"),
    ("trailer", r"trailer|причеп|причіп|cargo"),
    ("truck", r"\btrucks?\b|тягач"),
)

def classify_mod(categories: list, tags: list, name: str) -> tuple:
    """(категорія, джерело) з manifest.sii -> тегів Workshop -> назви. (None, None) — невідомо."""
    cats = [c for c in categories if c in _MOD_CAT_TIER]
    specific = [c for c in cats if c != "other"]
    if "paint_job" in specific:          # скін-паки часто пишуть truck/trailer перед paint_job
        return "paint_job", "manifest"
    if specific:
        return specific[0], "manifest"
    low = {t.strip().lower() for t in tags}
    for tag, cat in _WS_TAG_CATS:
        if tag in low:
            return cat, "workshop"
    if cats:
        return "other", "manifest"
    for cat, rx in _NAME_CAT_RULES:
        if re.search(rx, name or "", re.I):
            return cat, "name"
    if low:
        return "other", "workshop"
    return None, None

def _mod_name_words(name: str) -> list:
    s = re.sub(r'^\s*(?:[\[(][^\])]*[\])]\s*)+', '', name or '').lower()
    return re.findall(r'[^\W_]+', s)

def _same_mod_family(a: list, b: list) -> bool:
    """Моди однієї серії: спільний початок назви щонайменше з 2 слів і 8 символів
    («MAN TGX 2020 Rework» / «MAN TGX 2020 Tuning Parts», «Ukr map Def» / «Ukr map Model»)."""
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n >= 2 and len(" ".join(a[:n])) >= 8

def plan_mod_sort(items: list) -> dict:
    """items — увімкнені моди згори вниз (як у менеджері гри): [{'name', 'cat'}].
    {'order': індекси items у новому порядку, 'tier': рівень кожного, 'families': [[індекси]]}.
    Сортування стабільне: у межах рівня зберігається поточний порядок; серія модів іде одним блоком
    на рівні, який найчастіше трапляється серед її модів (за рівності — рівень найвищого з них)."""
    n = len(items)
    words = [_mod_name_words(it.get('name') or "") for it in items]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if _same_mod_family(words[i], words[j]):
                parent[find(j)] = find(i)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    default = _MOD_CAT_TIER["other"]
    tier = [default] * n
    units = []
    for members in groups.values():
        known = [_MOD_CAT_TIER[items[i]['cat']] for i in members if items[i].get('cat') in _MOD_CAT_TIER]
        t = default
        if known:
            counts = {k: known.count(k) for k in known}
            best = max(counts.values())
            t = next(k for k in known if counts[k] == best)
        for i in members:
            tier[i] = t
        units.append((t, members[0], members))
    units.sort(key=lambda u: (u[0], u[1]))
    return {'order': [i for u in units for i in u[2]], 'tier': tier,
            'families': [m for m in groups.values() if len(m) > 1]}

_ACTIVE_MODS_COUNT = re.compile(r'^(\s*)active_mods\s*:\s*(\d+)\s*$')
_ACTIVE_MODS_ITEM = re.compile(r'^(\s*)active_mods\[(\d*)\]\s*:\s*(.*?)\s*$')

def reorder_profile_mods(raw: bytes, order: list) -> bytes:
    """profile.sii з active_mods у порядку `order` (order[k] — який запис файлу стане active_mods[k]).
    Пишеться відкритим текстом SII (гра читає його так само, як зашифрований); інші рядки не змінюються."""
    if raw[:4] == SCSC_MAGIC:
        raw = _decrypt_scsc(raw) or b''
    if not raw.lstrip(b'\xef\xbb\xbf').startswith(b'SiiNunit'):
        raise ValueError("profile.sii is not a text SII file")
    text = raw.decode('utf-8')
    lines = text.split('\n')
    counts = [i for i, l in enumerate(lines) if _ACTIVE_MODS_COUNT.match(l.rstrip('\r'))]
    if len(counts) != 1:
        raise ValueError("active_mods not found in profile.sii")
    c = counts[0]
    n = int(_ACTIVE_MODS_COUNT.match(lines[c].rstrip('\r')).group(2))
    items = []
    for k in range(n):
        m = _ACTIVE_MODS_ITEM.match(lines[c + 1 + k].rstrip('\r')) if c + 1 + k < len(lines) else None
        if not m or m.group(2) not in ("", str(k)):
            raise ValueError("unexpected active_mods layout in profile.sii")
        items.append(m)
    others = lines[:c] + lines[c + 1 + n:]
    if any(_ACTIVE_MODS_ITEM.match(l.rstrip('\r')) for l in others):
        raise ValueError("unexpected active_mods layout in profile.sii")
    if sorted(order) != list(range(n)):
        raise ValueError("bad mod order")
    for k, src in enumerate(order):
        cr = '\r' if lines[c + 1 + k].endswith('\r') else ''
        lines[c + 1 + k] = f"{items[k].group(1)}active_mods[{k}]: {items[src].group(3)}{cr}"
    out = '\n'.join(lines)
    before = parse_sii_unit(text).get('active_mods') or []
    after = parse_sii_unit(out).get('active_mods') or []
    old_lines, new_lines = text.split('\n'), out.split('\n')
    if (after != [before[i] for i in order] or len(new_lines) != len(old_lines)
            or new_lines[:c + 1] != old_lines[:c + 1] or new_lines[c + 1 + n:] != old_lines[c + 1 + n:]):
        raise ValueError("verification failed")
    return out.encode('utf-8')

# ============================================================================
# РЕДАКТОР СЕЙВІВ: декодер бінарного SII (BSII) і точкове редагування полів
# ============================================================================
class BSIIError(Exception):
    pass

_TOKEN_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz_"

def decode_token(v: int) -> str:
    out = []
    while v:
        v, r = divmod(v, 38)
        out.append(_TOKEN_CHARS[r - 1] if r else "")
    return "".join(out)

class BSIIReader:
    """Послідовно читає BSII: визначення структур (блок 0) і юніти даних.
    Для кожного поля запам'ятовує зміщення, щоб скаляри можна було змінити на місці."""
    SCALARS = {
        0x05: '<f', 0x25: '<i', 0x27: '<I', 0x2B: '<H', 0x2F: '<I', 0x31: '<q', 0x33: '<Q',
        0x35: '<B', 0x37: '<I', 0x07: '<2f', 0x09: '<3f', 0x11: '<3i', 0x17: '<4f',
        0x19: '<8f',  # placement у BSII v3 — 8 float (перевірено на реальних сейвах ETS2/ATS)
    }
    ARRAYS = {
        0x02: 0x01, 0x04: 0x03, 0x06: 0x05, 0x08: 0x07, 0x0A: 0x09, 0x12: 0x11, 0x18: 0x17,
        0x1A: 0x19, 0x26: 0x25, 0x28: 0x27, 0x2C: 0x2B, 0x30: 0x2F, 0x32: 0x31, 0x34: 0x33,
        0x36: 0x35, 0x38: 0x37, 0x3A: 0x39, 0x3C: 0x3B, 0x3E: 0x3D,
    }

    def __init__(self, data: bytes):
        if data[:4] != b'BSII':
            raise BSIIError("not a binary SII")
        self.data = data
        self.version = struct.unpack_from('<I', data, 4)[0]
        self.pos = 8
        self.structs = {}

    def _u32(self):
        v = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def _string(self):
        n = self._u32()
        if n > 10_000_000:
            raise BSIIError(f"bad string length at {self.pos - 4}")
        v = self.data[self.pos:self.pos + n].decode('utf-8', 'replace')
        self.pos += n
        return v

    def _id(self):
        n = self.data[self.pos]
        self.pos += 1
        if n == 0xFF:
            v = struct.unpack_from('<Q', self.data, self.pos)[0]
            self.pos += 8
            return "_nameless." + ".".join(f"{(v >> s) & 0xFFFF:x}" for s in (48, 32, 16, 0)).lstrip("0.")
        parts = []
        for _ in range(n):
            parts.append(decode_token(struct.unpack_from('<Q', self.data, self.pos)[0]))
            self.pos += 8
        return ".".join(parts) if parts else "null"

    def _value(self, t):
        fmt = self.SCALARS.get(t)
        if fmt:
            v = struct.unpack_from(fmt, self.data, self.pos)
            self.pos += struct.calcsize(fmt)
            return v[0] if len(v) == 1 else v
        if t == 0x01:
            return self._string()
        if t == 0x03:
            v = struct.unpack_from('<Q', self.data, self.pos)[0]
            self.pos += 8
            return decode_token(v)
        if t in (0x39, 0x3B, 0x3D):
            return self._id()
        if t in self.ARRAYS:
            cnt = self._u32()
            if cnt > 5_000_000:
                raise BSIIError(f"bad array size at {self.pos - 4}")
            et = self.ARRAYS[t]
            return [self._value(et) for _ in range(cnt)]
        raise BSIIError(f"unknown value type 0x{t:x} at {self.pos}")

    def _struct_def(self):
        valid = self.data[self.pos]
        self.pos += 1
        if not valid:
            return
        sid = self._u32()
        name = self._string()
        fields = []
        while True:
            t = self._u32()
            if t == 0:
                break
            fname = self._string()
            table = None
            if t == 0x37:
                table = {}
                for _ in range(self._u32()):
                    k = self._u32()
                    table[k] = self._string()
            fields.append((t, fname, table))
        self.structs[sid] = (name, fields)

    def units(self):
        """(назва структури, id, {поле: (значення, зміщення, тип)}) — до кінця файлу."""
        try:
            while self.pos < len(self.data):
                bt = self._u32()
                if bt == 0:
                    self._struct_def()
                    continue
                if bt not in self.structs:
                    raise BSIIError(f"unknown structure {bt} at {self.pos - 4}")
                name, fields = self.structs[bt]
                uid = self._id()
                vals = {}
                for t, fname, table in fields:
                    off = self.pos
                    v = self._value(t)
                    if table is not None:
                        v = table.get(v, v)
                    vals[fname] = (v, off, t)
                yield name, uid, vals
        except struct.error as e:
            raise BSIIError(f"unexpected end of data: {e}")
        if self.pos != len(self.data):
            raise BSIIError("trailing data after last unit")

SAVE_EDIT_FIELDS = {
    # поле: (юніт, тип у BSII, мін, макс)
    "money_account": ("bank", 0x31, -100_000_000, 2_000_000_000),
    "experience_points": ("economy", 0x27, 0, 4_000_000_000),
    "adr": ("economy", 0x27, 0, 63),
    "long_dist": ("economy", 0x27, 0, 6),
    "heavy": ("economy", 0x27, 0, 6),
    "fragile": ("economy", 0x27, 0, 6),
    "urgent": ("economy", 0x27, 0, 6),
    "mechanical": ("economy", 0x27, 0, 6),
}

def load_save_data(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw[:4] == SCSC_MAGIC:
        raw = _decrypt_scsc(raw)
        if raw is None:
            raise BSIIError("decryption failed")
    if raw[:4] != b'BSII':
        raise BSIIError("game.sii is not in the binary format (BSII)")
    return raw

def read_save_fields(data: bytes) -> dict:
    """{поле: (значення, зміщення)} для SAVE_EDIT_FIELDS. Файл обов'язково читається до кінця."""
    found = {}
    for name, _uid, vals in BSIIReader(data).units():
        for field, (unit, t, _lo, _hi) in SAVE_EDIT_FIELDS.items():
            if name == unit and field in vals and vals[field][2] == t:
                if field in found:
                    raise BSIIError(f"field {field} found twice")
                found[field] = (vals[field][0], vals[field][1])
    missing = set(SAVE_EDIT_FIELDS) - set(found)
    if missing:
        raise BSIIError(f"fields not found: {', '.join(sorted(missing))}")
    return found

def patch_save_fields(data: bytes, changes: dict) -> bytes:
    """Змінює лише байти потрібних полів і перевіряє результат повторним декодуванням."""
    fields = read_save_fields(data)
    out = bytearray(data)
    for field, value in changes.items():
        _unit, t, lo, hi = SAVE_EDIT_FIELDS[field]
        value = int(value)
        if not lo <= value <= hi:
            raise ValueError(f"{field}: {value} out of range {lo}..{hi}")
        struct.pack_into(BSIIReader.SCALARS[t], out, fields[field][1], value)
    out = bytes(out)
    check = read_save_fields(out)
    for field, value in changes.items():
        if check[field][0] != int(value):
            raise BSIIError(f"verification failed for {field}")
    if len(out) != len(data) or sum(1 for a, b in zip(out, data) if a != b) > 8 * len(changes):
        raise BSIIError("verification failed: unexpected changes")
    return out

# ============================================================================
# СЕРВЕР КОНВОЮ: server_config.sii (межі — за документацією SCS Dedicated Server)
# ============================================================================
SERVER_FIELDS = (
    # (ключ, тип, параметри)
    ("lobby_name", "str", 63), ("description", "str", 63), ("welcome_message", "str", 127),
    ("password", "str", 63), ("max_players", "int", (1, 8)), ("timezones", "int", (0, 2)),
    ("max_vehicles_total", "int", (0, 100000)), ("max_ai_vehicles_player", "int", (0, 100000)),
    ("max_ai_vehicles_player_spawn", "int", (0, 100000)), ("traffic", "bool", None),
    ("player_damage", "bool", None), ("hide_in_company", "bool", None), ("hide_colliding", "bool", None),
    ("force_speed_limiter", "bool", None), ("mods_optioning", "bool", None),
    ("service_no_collision", "bool", None), ("in_menu_ghosting", "bool", None), ("name_tags", "bool", None),
    ("friends_only", "bool", None), ("show_server", "bool", None),
    ("connection_virtual_port", "int", (100, 200)), ("query_virtual_port", "int", (100, 200)),
    ("connection_dedicated_port", "int", (1, 65535)), ("query_dedicated_port", "int", (1, 65535)),
    ("server_logon_token", "str", 128), ("moderator_list", "ids", None),
)

def read_server_config(path: Path) -> dict:
    data = parse_sii_unit(path.read_text(encoding='utf-8', errors='ignore'))
    values = {}
    for key, kind, _ in SERVER_FIELDS:
        raw = data.get(key)
        if raw is None:
            continue
        if kind == "bool":
            values[key] = str(raw).lower() == "true"
        elif kind == "int":
            try:
                values[key] = int(raw)
            except ValueError:
                pass
        elif kind == "ids":
            values[key] = list(raw) if isinstance(raw, list) else []
        else:
            values[key] = raw
    return values

def validate_server_config(values: dict) -> list:
    """Список проблем (порожній — усе гаразд)."""
    errors = []
    for key, kind, param in SERVER_FIELDS:
        if key not in values:
            continue
        v = values[key]
        if kind == "str" and len(v.encode('utf-8')) > param:
            errors.append((key, "len", param))
        elif kind == "int" and not param[0] <= v <= param[1]:
            errors.append((key, "range", param))
        elif kind == "ids" and any(not re.fullmatch(r'\d{1,20}', x) for x in v):
            errors.append((key, "ids", None))
    if values.get("connection_virtual_port") == values.get("query_virtual_port"):
        errors.append(("query_virtual_port", "same_port", None))
    if values.get("connection_dedicated_port") == values.get("query_dedicated_port"):
        errors.append(("query_dedicated_port", "same_port", None))
    return errors

def write_server_config(path: Path, values: dict):
    """Оновлює лише відомі поля; решта рядків файлу залишається як була."""
    text = path.read_bytes().decode('utf-8', errors='ignore')
    nl = "\r\n" if "\r\n" in text else "\n"
    kinds = {k: kind for k, kind, _ in SERVER_FIELDS}

    def fmt(key):
        v = values[key]
        if kinds[key] == "bool":
            return "true" if v else "false"
        if kinds[key] == "int":
            return str(int(v))
        return f'"{sii_escape(v)}"'

    out, written = [], set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _SII_LINE.match(line)
        key = m.group(1) if m else None
        if key in values:
            if kinds[key] == "ids":
                if m.group(2) is None:
                    out.append(f" {key}: {len(values[key])}")
                    out.extend(f" {key}[{n}]: {sid}" for n, sid in enumerate(values[key]))
                    written.add(key)
                continue
            out.append(f" {key}: {fmt(key)}")
            written.add(key)
            continue
        if line.strip() == "}" and any(l.strip() == "}" for l in lines[i + 1:]):
            # Кінець юніта: дописуємо поля, яких у файлі не було
            for k in values:
                if k not in written:
                    if kinds[k] == "ids":
                        out.append(f" {k}: {len(values[k])}")
                        out.extend(f" {k}[{n}]: {sid}" for n, sid in enumerate(values[k]))
                    else:
                        out.append(f" {k}: {fmt(k)}")
                    written.add(k)
        out.append(line)
    path.write_bytes((nl.join(out) + nl).encode('utf-8'))

# ============================================================================
# ІНТЕРНЕТ-РАДІО: live_streams.sii
# ============================================================================
RADIO_FIELDS = ("url", "name", "genre", "lang", "bitrate", "favorite")

def sii_escape(val: str) -> str:
    r"""Зворотне до sii_unescape: лапки -> \", не-ASCII -> \xNN (UTF-8)."""
    val = val.replace('\\', '/').replace('"', '\\"')
    return "".join(c if ord(c) < 128 else "".join(f"\\x{b:02x}" for b in c.encode('utf-8')) for c in val)

def parse_radio_entry(raw: str) -> dict:
    parts = (raw.split('|') + [""] * len(RADIO_FIELDS))[:len(RADIO_FIELDS)]
    return dict(zip(RADIO_FIELDS, parts))

def format_radio_entry(st: dict) -> str:
    clean = lambda v: str(v or "").replace('|', '/').replace('\r', ' ').replace('\n', ' ')
    return "|".join(clean(st.get(f, "")) for f in RADIO_FIELDS)

def read_live_streams(path: Path) -> tuple:
    """(назва юніта, [станції]) з live_streams.sii."""
    text = path.read_text(encoding='utf-8', errors='ignore')
    m = re.search(r'live_stream_def\s*:\s*(\S+)\s*\{', text)
    data = parse_sii_unit(text)
    return (m.group(1) if m else "_nameless.radio"), [parse_radio_entry(x) for x in data.get('stream_data', []) or []]

def write_live_streams(path: Path, unit_name: str, stations: list):
    lines = ["SiiNunit", "{", f"live_stream_def : {unit_name} {{", f" stream_data: {len(stations)}"]
    lines += [f' stream_data[{i}]: "{sii_escape(format_radio_entry(st))}"' for i, st in enumerate(stations)]
    lines += ["}", "", "}", ""]
    path.write_bytes("\r\n".join(lines).encode('utf-8'))

def _radio_store_file(game_name: str) -> Path:
    return _workshop_cache_file().parent / f"my_radio_{STEAM_APPS.get(game_name, 0)}.json"

def load_my_radio(game_name: str) -> list:
    try:
        return [parse_radio_entry(x) for x in json.loads(_radio_store_file(game_name).read_text(encoding='utf-8'))]
    except Exception:
        return []

def save_my_radio(game_name: str, stations: list):
    f = _radio_store_file(game_name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([format_radio_entry(s) for s in stations], ensure_ascii=False, indent=1), encoding='utf-8')

def check_radio_stream(url: str, timeout: float = 6.0) -> tuple:
    """(ok, опис). Вважаємо живим потік, що відповідає 200 і віддає аудіо (або ICY-заголовки)."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': f'TruckControlsManager/{APP_VERSION}', 'Icy-MetaData': '1'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get('Content-Type') or "").split(';')[0].strip().lower()
            icy = any(k.lower().startswith('icy-') for k in resp.headers.keys())
            resp.read(512)
            ok = resp.status == 200 and (ctype.startswith('audio/') or ctype in ('application/ogg', 'application/octet-stream') or icy)
            return ok, f"{resp.status} {ctype or ('icy' if icy else '?')}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        reason = getattr(e, 'reason', None) or e
        return False, str(reason)[:40]

# Пошук станцій: відкритий каталог Radio Browser (radio-browser.info, без ключа).
# Гра програє прямі потоки MP3 та AAC (AAC є і в стандартному списку SCS); HLS і плейлисти — ні.
RADIO_BROWSER_HOSTS = ("all.api.radio-browser.info", "de1.api.radio-browser.info",
                       "de2.api.radio-browser.info", "fi1.api.radio-browser.info")
RADIO_GAME_CODECS = ("MP3", "AAC", "AAC+")
RADIO_COUNTRIES = ("UA", "PL", "DE", "GB", "US", "CA", "CZ", "SK", "LT", "LV", "EE", "MD", "RO", "HU", "AT",
                   "CH", "NL", "BE", "FR", "IT", "ES", "PT", "SE", "NO", "FI", "DK", "GR", "BG", "HR", "RS", "TR")

def radio_browser_station(s: dict) -> dict | None:
    """Запис каталогу -> станція для live_streams.sii (None, якщо гра її не програє)."""
    codec = (s.get('codec') or "").upper()
    url = (s.get('url_resolved') or s.get('url') or "").strip()
    if codec not in RADIO_GAME_CODECS or s.get('hls') or not re.match(r'^https?://[^\s|"]+$', url):
        return None
    if re.search(r'\.(m3u8?|pls|asx|xspf)(\?|$)', url, re.I):
        return None
    tags = [t.strip() for t in (s.get('tags') or "").split(',') if t.strip()]
    genre = ", ".join(t[:1].upper() + t[1:] for t in tags[:2])[:40] or "Radio"
    name = re.sub(r'\s+', ' ', s.get('name') or "").strip()[:60]
    if not name:
        return None
    try:
        bitrate = int(s.get('bitrate') or 0)
    except (TypeError, ValueError):
        bitrate = 0
    return {'url': url, 'name': name, 'genre': genre, 'lang': (s.get('countrycode') or "INT").upper()[:3],
            'bitrate': str(bitrate or 128), 'favorite': "0",
            'votes': int(s.get('votes') or 0), 'codec': codec, 'uuid': s.get('stationuuid') or ""}

def search_radio_browser(name: str = "", tag: str = "", country: str = "", limit: int = 150, timeout: float = 12) -> list:
    """Станції з каталогу, які гра здатна програти, найпопулярніші першими."""
    params = {"hidebroken": "true", "order": "votes", "reverse": "true", "limit": str(limit * 2)}
    if name:
        params["name"] = name
    if tag:
        params["tag"] = tag.lower()
    if country:
        params["countrycode"] = country
    query = urllib.parse.urlencode(params)
    last_error = None
    for host in RADIO_BROWSER_HOSTS:
        req = urllib.request.Request(f"https://{host}/json/stations/search?{query}",
                                     headers={'User-Agent': f'TruckControlsManager/{APP_VERSION}'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='ignore'))
            break
        except Exception as e:
            last_error = e
    else:
        raise ConnectionError(str(getattr(last_error, 'reason', None) or last_error))
    result, seen = [], set()
    for s in data:
        st = radio_browser_station(s)
        if st and st['url'] not in seen:
            seen.add(st['url'])
            result.append(st)
    return result[:limit]

# ============================================================================
# АНАЛІЗ game.log.txt ТА game.crash.txt
# ============================================================================
_LOG_LINE_RE = re.compile(r'^\s*([\d:.]+)\s*:\s*<(ERROR|WARNING)>\s*(.*)$')
_LOG_MOD_RE = re.compile(
    r"[/\\]mod[/\\]([^/\\|']+?\.(?:scs|zip))"
    r"|workshop[/\\]content[/\\]\d+[/\\](\d+)"
    r"|mod_workshop_package\.([0-9A-Fa-f]+)", re.I)

def _log_mod_name(msg: str, workshop_names: dict) -> str | None:
    m = _LOG_MOD_RE.search(msg)
    if not m:
        return None
    if m.group(1):
        return m.group(1).strip()
    ws_id = m.group(2) or str(int(m.group(3), 16))
    return workshop_names.get(ws_id) or f"Workshop #{ws_id}"

def _log_group_key(msg: str) -> str:
    tag = re.match(r'\[([\w ]+)\]', msg)
    target = re.search(r"'([^']+)'|\(([^)]+)\)", msg)
    if target:
        prefix = f"[{tag.group(1)}] " if tag else ""
        return prefix + (target.group(1) or target.group(2))
    return re.sub(r'\b[0-9a-f]{3,}(\.[0-9a-f]+)+\b|\d+', '#', msg)[:120]

def group_log_problems(lines: list, workshop_names: dict) -> list:
    """Групує <ERROR>/<WARNING> за модом-джерелом або за файлом/повідомленням.
    Рядок-продовження («File '...mod...':» + деталь у наступному рядку) йде в ту ж групу."""
    groups = {}
    prev_mod, prev_open = None, False
    for line in lines:
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        _, level, msg = m.groups()
        mod = _log_mod_name(msg, workshop_names)
        if mod is None and prev_open and prev_mod:
            mod = prev_mod
        key = ("mod", mod) if mod else ("game", _log_group_key(msg))
        g = groups.setdefault(key, {"kind": key[0], "title": key[1], "errors": 0, "warnings": 0, "sample": msg})
        g["errors" if level == "ERROR" else "warnings"] += 1
        prev_mod, prev_open = mod, msg.rstrip().endswith(':')
    return sorted(groups.values(), key=lambda g: (g["kind"] != "mod", -(g["errors"] * 1000 + g["warnings"])))

_SYSTEM_DIR_RE = re.compile(r'^[A-Za-z]:\\Windows\\', re.I)

def parse_crash_log(text: str) -> list:
    """Розбирає накопичувальний game.crash.txt на окремі збої (від старих до нових)."""
    crashes = []
    for chunk in re.split(r'^Crash log created on:', text, flags=re.M)[1:]:
        def field(name):
            m = re.search(rf'^{name}:\s*(.+)$', chunk, re.M)
            return m.group(1).strip() if m else ""
        fault = re.search(r'^Fault address:\s*\S+\s+\S+\s+(.+)$', chunk, re.M)
        modules = set()
        rtl = re.search(r'Call stack \(RTL\):(.*?)(?:\n\s*\n|^Stack:)', chunk, re.S | re.M)
        for path in re.findall(r'^[0-9A-F]{16}\s+\S+\s+(.+)$', rtl.group(1) if rtl else "", re.M):
            path = path.strip()
            name = re.split(r'[\\/]', path)[-1]
            if not _SYSTEM_DIR_RE.match(path) and name.lower() not in ("eurotrucks2.exe", "amtrucks.exe"):
                modules.add(name)
        loaded = []
        msec = re.search(r'^Modules:\n(.*?)(?:\n\s*\n|\Z)', chunk, re.S | re.M)
        for line in (msec.group(1).splitlines() if msec else []):
            parts = line.split(None, 3)
            if len(parts) == 4 and not _SYSTEM_DIR_RE.match(parts[3].strip()):
                loaded.append(parts[3].strip())
        avail = re.search(r'^AvailMEM .*?Phys:(\d+)', chunk, re.M)
        try:
            when = datetime.strptime(" ".join(chunk.splitlines()[0].split()), "%a %b %d %H:%M:%S %Y")
        except Exception:
            when = None
        crashes.append({
            "when": when,
            "build": field("Build").split()[0] if field("Build") else "",
            "gpu": field("GPU"),
            "exception": field("Exception code"),
            "fault_module": re.split(r'[\\/]', fault.group(1).strip())[-1] if fault else "",
            "third_party": sorted(modules),
            "modules": loaded,
            "avail_phys": int(avail.group(1)) if avail else None,
            "os": field("OS"),
        })
    return crashes

# ============================================================================
# ДІАГНОСТИКА: відомі модулі у збоях, пояснення кодів, net.log, пошук в інтернеті
# ============================================================================
# Модулі, що часто стають причиною вильотів ігор SCS (оверлеї, хуки, інжектори).
# (ключ перекладу, регулярний вираз імені файлу)
KNOWN_CRASH_MODULES = (
    ("rtss", r"^rtsshooks(64)?\.dll$"),
    ("discord", r"^discordhook(64)?\.dll$"),
    ("steam_overlay", r"^gameoverlayrenderer(64)?\.dll$"),
    ("asus", r"^(aurasdk_x64|aac\w*hal_x64)\.dll$"),
    ("headtrack", r"^npclient(64)?\.dll$"),
    ("obs", r"^(graphics-hook(32|64)|obs-vulkan(32|64))\.dll$"),
    ("overwolf", r"^(ow_|overwolf)\w*\.dll$"),
    ("reshade", r"^reshade(32|64)?\.dll$"),
    ("nvidia", r"^(nvwgf2umx|nvldumdx|nvd3dumx|nvoglv64|nvgpucomp64)\.dll$"),
    ("amd", r"^(amdxx64|atidxx64|amdxc64|atiumd6a|aticfx64|amd_ags_x64)\.dll$"),
)
# Імена DLL, якими ReShade, ASI-лоадери, DLSS/FSR-підміни тощо підкладаються в папку гри
INJECTOR_DLL_NAMES = {"dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll", "dinput8.dll", "version.dll",
                      "winmm.dll", "opengl32.dll", "xinput1_3.dll", "xinput1_4.dll"}
# Власні бібліотеки гри — не підозрювані
GAME_OWN_DLLS = {"eurotrucks2.exe", "amtrucks.exe", "fmod.dll", "fmodstudio.dll", "steam_api64.dll",
                 "tobii_gameintegration_x64.dll", "thrustmaster_bridge_x64.dll", "dstorage.dll",
                 "dstoragecore.dll", "nvngx_dlss.dll", "hidapi.dll", "libcef.dll"}
EXCEPTION_KEYS = {"C0000005": "exc_access", "C0000025": "exc_noncont", "C00000FD": "exc_stack",
                  "C0000409": "exc_overrun", "C000001D": "exc_illegal", "E06D7363": "exc_cpp",
                  "80000003": "exc_break", "C0000374": "exc_heap", "C0000017": "exc_nomem"}

def classify_crash_module(path: str) -> str | None:
    """Ключ відомого проблемного модуля або 'injector' для сторонньої DLL у папці гри."""
    name = re.split(r'[\\/]', path)[-1].lower()
    for key, pattern in KNOWN_CRASH_MODULES:
        if re.match(pattern, name):
            return key
    low = path.lower().replace('/', '\\')
    if '\\bin\\win_x64\\' in low and name not in GAME_OWN_DLLS and (
            name in INJECTOR_DLL_NAMES or name.endswith('.asi')):
        return "injector"
    return None

def crash_suspects(crashes: list) -> list:
    """[(ключ, ім'я файлу, у скількох збоях завантажений, чи був у стеку)] — найчастіші першими."""
    found = {}
    for c in crashes:
        per_crash = {}
        for path in c.get('modules', []):
            key = classify_crash_module(path)
            if key:
                name = re.split(r'[\\/]', path)[-1]
                # Набір модулів одного продукту (напр. ASUS) — один підозрюваний; DLL у папці гри — кожна окремо
                group = (key, name.lower()) if key == "injector" else (key, "")
                per_crash.setdefault(group, set()).add(name)
        stack = {n.lower() for n in c.get('third_party', [])}
        for group, names in per_crash.items():
            rec = found.setdefault(group, [group[0], set(), 0, False])
            rec[1] |= names
            rec[2] += 1
            rec[3] = rec[3] or any(n.lower() in stack for n in names)
    result = [(k, ", ".join(sorted(n)[:3]) + (f" +{len(n) - 3}" if len(n) > 3 else ""), cnt, st)
              for k, n, cnt, st in found.values()]
    # Спершу ті, що у стеку; далі — відоміші винуватці вильотів; далі — за частотою
    risk = {"injector": 0, "rtss": 0, "reshade": 0, "discord": 1, "obs": 1, "overwolf": 1,
            "headtrack": 2, "asus": 2, "nvidia": 3, "amd": 3, "steam_overlay": 4}
    return sorted(result, key=lambda r: (not r[3], risk.get(r[0], 2), -r[2]))

_NET_LINE = re.compile(r'^(\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d)\s+(\w+)\s+(\w+):\s*(.*)$')
WSA_CODES = {"10060": "net_timeout", "10061": "net_refused", "10054": "net_reset", "10051": "net_unreach",
             "10065": "net_unreach", "10053": "net_reset", "11001": "net_dns", "11004": "net_dns"}

def parse_net_log(text: str) -> dict:
    """Розбір net.log: сесії та згруповані мережеві помилки."""
    sessions, groups = 0, {}
    for line in text.splitlines():
        m = _NET_LINE.match(line.strip())
        if not m:
            continue
        when, cat, level, msg = m.groups()
        if "Started" in msg and level.startswith("INF"):
            sessions += 1
        if not level.startswith("ERR"):
            continue
        host = None
        hm = re.search(r"(?:getaddinfo\('|for host '|connect\('|to 'https?://)([^'/:]+)", msg)
        if hm:
            host = hm.group(1)
        if cat == "LINK":
            # Зв'язок гри з World of Trucks
            kind = {"access denied": "wot_denied", "not found": "wot_notfound", "network error": "wot_network",
                    "game is not registered": "wot_unregistered"}.get(msg.strip().rstrip('.').lower(), "wot_other")
        elif "getaddinfo" in msg or "to any address for host" in msg:
            kind = "net_dns"
        elif "reading from socket" in msg:
            kind = "net_reset"
        else:
            cm = re.search(r"failed (\d{5})", msg)
            kind = WSA_CODES.get(cm.group(1), "net_other") if cm else (
                "net_http" if "Unable to connect" in msg else "net_other")
        g = groups.setdefault(kind, {"kind": kind, "count": 0, "hosts": set(), "first": when, "last": when})
        g["count"] += 1
        g["last"] = when
        if host:
            g["hosts"].add(host)
    order = sorted(groups.values(), key=lambda g: -g["count"])
    return {"sessions": sessions, "groups": order, "errors": sum(g["count"] for g in order)}

NET_CHECK_HOSTS = ("eut2.online.scssoft.com", "api.steampowered.com", "steamcommunity.com", "www.google.com")

def run_network_check() -> list:
    """[(ok, назва перевірки, деталі)] — DNS і HTTP до серверів SCS і Steam."""
    import socket
    results = []
    for host in NET_CHECK_HOSTS:
        t0 = time.time()
        try:
            ip = socket.getaddrinfo(host, 443)[0][4][0]
            results.append((True, f"DNS {host}", f"{ip} · {int((time.time() - t0) * 1000)} ms"))
        except Exception as e:
            results.append((False, f"DNS {host}", str(e)[:60]))
            continue
        url = "http://eut2.online.scssoft.com/" if host.startswith("eut2") else f"https://{host}/"
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': f'TruckControlsManager/{APP_VERSION}'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                code = resp.status
            results.append((True, f"HTTP {url}", f"{code} · {int((time.time() - t0) * 1000)} ms"))
        except urllib.error.HTTPError as e:
            # Сервер відповів (навіть 4xx) — з'єднання працює
            results.append((True, f"HTTP {url}", f"{e.code} · {int((time.time() - t0) * 1000)} ms"))
        except Exception as e:
            reason = getattr(e, 'reason', None) or e
            results.append((False, f"HTTP {url}", str(reason)[:60]))
    return results

def web_search_url(query: str, site: str | None = None) -> str:
    q = f"site:{site} {query}" if site else query
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)

def get_dir_size_str(path: Path) -> str:
    if not path.exists():
        return "0 KB"
    try:
        total = sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
        if total > 1024 * 1024 * 1024:
            return f"{total / (1024 * 1024 * 1024):.2f} GB"
        if total > 1024 * 1024:
            return f"{total / (1024 * 1024):.1f} MB"
        return f"{total / 1024:.1f} KB"
    except Exception:
        return "N/A"

def get_app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def get_resource_path(relative_path: str) -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        p = Path(sys._MEIPASS) / relative_path
        if p.exists():
            return p
    return get_app_dir() / relative_path

def get_documents_dir() -> Path:
    """Справжня папка «Документи» (враховує перенесення, напр. в OneDrive)."""
    try:
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL = 5
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / 'Documents'

# ============================================================================
# STEAM: пошук встановлення, параметрів запуску (-homedir) та Steam Cloud профілів
# ============================================================================
_steam_dir_cache = []

def find_steam_dir() -> Path | None:
    if _steam_dir_cache:
        return _steam_dir_cache[0]
    found = None
    try:
        import winreg
        for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    p = Path(winreg.QueryValueEx(k, value)[0])
                if (p / 'userdata').is_dir():
                    found = p
                    break
            except OSError:
                continue
    except Exception:
        pass
    _steam_dir_cache.append(found)
    return found

def steam_userdata_dirs() -> list:
    steam = find_steam_dir()
    if not steam:
        return []
    try:
        return [d for d in (steam / 'userdata').iterdir() if d.is_dir() and d.name.isdigit()]
    except Exception:
        return []

def _parse_vdf(text: str) -> dict:
    """Мінімальний парсер текстового VDF (формат конфігів Steam)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root, stack, key = {}, [], None
    cur = root
    for s, brace in tokens:
        if brace == '{':
            new = {}
            if key is not None:
                cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == '}':
            cur = stack.pop() if stack else root
            key = None
        else:
            s = s.replace('\\\\', '\x00').replace('\\"', '"').replace('\x00', '\\')
            if key is None:
                key = s
            else:
                cur[key] = s
                key = None
    return root

def _vdf_get_ci(d: dict, *path):
    for part in path:
        if not isinstance(d, dict):
            return None
        d = next((v for k, v in d.items() if k.lower() == part.lower()), None)
    return d

def find_homedir_from_steam(game_name: str) -> Path | None:
    """Шукає -homedir у параметрах запуску гри в Steam (усі акаунти, найсвіжіший першим)."""
    appid = str(STEAM_APPS.get(game_name, ''))
    configs = [d / 'config' / 'localconfig.vdf' for d in steam_userdata_dirs()]
    configs = sorted((c for c in configs if c.exists()), key=lambda c: c.stat().st_mtime, reverse=True)
    for cfg in configs:
        try:
            data = _parse_vdf(cfg.read_text(encoding='utf-8', errors='ignore'))
        except Exception:
            continue
        opts = _vdf_get_ci(data, 'UserLocalConfigStore', 'Software', 'Valve', 'Steam', 'apps', appid, 'LaunchOptions')
        if not isinstance(opts, str):
            continue
        m = re.search(r'-homedir\s+(?:"([^"]+)"?|(\S+))', opts, re.I)
        if not m:
            continue
        base = (m.group(1) or m.group(2)).strip().strip('"').rstrip('\\/')
        if base:
            return Path(base) / GAMES.get(game_name, game_name)
    return None

def find_steam_cloud_profile_dir(game_name: str, profile_id: str) -> Path | None:
    """Steam Cloud профіль тримає config.cfg, profile.sii та save у Steam\\userdata\\<акаунт>\\<appid>\\remote."""
    appid = str(STEAM_APPS.get(game_name, ''))
    candidates = [d / appid / 'remote' / 'profiles' / profile_id for d in steam_userdata_dirs()]
    candidates = [c for c in candidates if c.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.stat().st_mtime)

def get_logs_dir() -> Path:
    d = get_documents_dir() / 'TruckControlsManager_Logs'
    d.mkdir(parents=True, exist_ok=True)
    return d

def setup_logger():
    log_file = get_logs_dir() / f"activity_{datetime.now().strftime('%Y%m%d')}.log"
    logger = logging.getLogger('TruckControlsManager')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s | %(message)s', '%Y-%m-%d %H:%M:%S'))
        logger.addHandler(fh)
    return logger

LANGUAGES = {
    "uk": {
        "title": "Truck Controls Manager",
        "subtitle": "Центр керування налаштуваннями, пресетами та сейвами ETS 2 / ATS",
        "tab_controls": "🎮 Керування",
        "tab_tweaks": "🚦 Твіки & Камера",
        "tab_saves": "💾 Профіль і сейви",
        "tab_mods": "📦 Моди",
        "tab_radio": "📻 Радіо",
        "radio_title": "Інтернет-радіо гри (live_streams.sii)",
        "radio_hint": "«🌐 Знайти станції» — пошук в Інтернеті з автододаванням робочих. Додані тут станції програма запам'ятовує окремо. Якщо кнопка гри «Оновити з Інтернету» їх затре — натисніть «↺ Мої станції». «Перевірити» — вибрані рядки, «Перевірити всі» — усі показані. Подвійний клік — додати/прибрати з обраного. Клік по заголовку — сортування.",
        "radio_summary": "Станцій: {}  ·  обраних ★: {}  ·  моїх (жовті): {}  ·  показано: {}",
        "radio_mine_lost": "⚠ Ваших станцій немає у файлі гри: {} — натисніть «↺ Мої станції».",
        "radio_no_file": "Файл live_streams.sii ще не створено — запустіть гру хоча б раз.",
        "radio_search_placeholder": "Пошук за назвою, жанром, мовою або адресою...",
        "radio_all_langs": "Усі мови",
        "radio_col_fav": "★",
        "radio_col_name": "Назва",
        "radio_col_genre": "Жанр",
        "radio_col_lang": "Мова",
        "radio_col_bitrate": "kbps",
        "radio_col_status": "Перевірка",
        "radio_col_url": "Адреса потоку",
        "btn_radio_add": "➕ Додати",
        "btn_radio_fav": "★ Обране",
        "btn_radio_check": "🔎 Перевірити",
        "btn_radio_check_all": "🔎 Перевірити всі",
        "btn_radio_delete": "🗑 Видалити",
        "btn_radio_reapply": "↺ Мої станції",
        "btn_faq": "❓ FAQ",
        "faq_title": "Часті питання",
        "faq_search_ph": "Пошук у питаннях і відповідях (наприклад: радіо, вилітає, сейв)...",
        "faq_nothing": "Нічого не знайдено — спробуйте інше слово.",
        "faq_go_tab": "→ Відкрити вкладку «{}»",
        "faq_footer": "Не знайшли відповідь? Вкладка «Діагностика» → «📋 Копіювати звіт» — і опишіть проблему на форумі SCS.",
        "faq_q1": "Програма не бачить мою гру або профілі. Що робити?",
        "faq_a1": "Папку гри програма шукає сама: у «Документах», у параметрі запуску Steam -homedir і в профілях Steam Cloud. Якщо ви перенесли дані гри в іншу папку, натисніть 📁 біля поля «Папка гри» і вкажіть папку, де лежать config.cfg та profiles або steam_profiles. Кнопка ↗ відкриває цю папку в Провіднику.",
        "faq_q2": "Що означає позначка [Steam Cloud] біля профілю?",
        "faq_a2": "Профіль синхронізується через Steam Cloud, тому його файли (config.cfg, profile.sii, сейви) лежать у папці Steam userdata\\…\\remote. Програма сама знаходить і змінює саме ці файли, а Steam вивантажить зміни в хмару під час наступного запуску гри. Бекапи таких файлів зберігаються в папці гри в Config_Backups, щоб не потрапити в хмару.",
        "faq_q3": "Я змінив налаштування, а в грі нічого не змінилося.",
        "faq_a3": "Гра читає свої файли при запуску, а при виході перезаписує їх своїми значеннями. Тому порядок такий: закрийте гру → змініть налаштування → запустіть гру. Індикатор угорі вікна показує, чи гра зараз запущена. Частина твіків (поліція, втома, фізика) зберігається в профілі — перевірте, що вибрано потрібний профіль.",
        "faq_q4": "Чи безпечно це для моїх збережень? Як повернути все назад?",
        "faq_a4": "Перед кожним записом програма робить копію файлу (ім'я.bak_дата, зберігаються 5 останніх). Перед редагуванням сейву створюється ZIP-архів слота «_pre_edit_…», а пресет керування перед застосуванням робить резервну копію. Щоб відновити файл, перейменуйте .bak-копію назад або відновіть архів у «Профіль і сейви» → «ZIP-архіви». Перед великими змінами варто створити повний ZIP-архів збережень.",
        "faq_q5": "Як перенести керування (кермо, геймпад) в інший профіль або другові?",
        "faq_a5": "Вкладка «Керування»: «💾 Зберегти як пресет» зберігає controls.sii, config.cfg, config_local.cfg і global_controls.sii. Потім виберіть інший профіль і застосуйте пресет. Пресет можна експортувати у файл і передати другові — він імпортує його кнопкою «📥 Імпорт пресету».",
        "faq_q6": "Після оновлення гри або видалення модів на біржі немає вантажів.",
        "faq_a6": "Вкладка «Твіки & Камера» → «🔄 Скинути біржу вантажів». Запустіть гру й завантажте профіль — ринок вантажів згенерується заново. Після цього натисніть кнопку ще раз, щоб повернути значення в 0.",
        "faq_q7": "Відредагований сейв не завантажується.",
        "faq_a7": "Редактор записує сейв у розшифрованому вигляді — гра такі відкриває. Якщо щось пішло не так, відновіть архів «_pre_edit_…» у «Профіль і сейви» → «ZIP-архіви». Гроші в меню завантаження оновлюються лише після наступного збереження в грі — це нормально.",
        "faq_q8": "Що означають «Відсутні» та «Без версії для поточної гри» в модах?",
        "faq_a8": "«Відсутній» — мод увімкнено в профілі, але його файлу немає на диску (ви відписалися у Workshop або видалили його з папки mod). Гра його пропустить; вимкніть мод у менеджері модів або встановіть знову. «Без версії для поточної гри» — автор Workshop-мода ще не випустив оновлення для вашої версії гри; такий мод може не працювати або спричиняти вильоти.",
        "faq_q9": "Як працює автосортування модів? Що робити, якщо воно переставило неправильно?",
        "faq_a9": "Категорію кожного мода програма бере з його manifest.sii, тегів Steam Workshop або назви і розставляє: інтерфейс, графіка, звук — угорі; скіни, тюнінг, тягачі, причепи — посередині; карти та моди-залежності — внизу. Моди однієї серії лишаються разом, ваш порядок у межах групи зберігається. Правила загальні: якщо автор карти чи збірки вимагає свого порядку, дотримуйтеся його. «↶ Скасувати» повертає попередній порядок, а бекап profile.sii зберігається.",
        "faq_q10": "Програма видаляє невикористані моди?",
        "faq_a10": "Ні. «Моди» → «Невикористані» лише показує моди, що не ввімкнені в жодному профілі, з їхнім розміром. Відписатися від Workshop-модів можна в Steam (кнопка «Підписки Workshop»), а файли з папки mod — видалити вручну.",
        "faq_q11": "Я додав радіостанцію, але в грі її немає або вона не грає.",
        "faq_a11": "Список станцій гра читає під час запуску — перезапустіть гру. Гра програє прямі потоки MP3/AAC; посилання на сторінку сайту чи плейлист (.m3u, .pls) не працюють. Перевірте станцію кнопкою «🔎 Перевірити». Деякі станції доступні лише у своїй країні. Найпростіше — «🌐 Знайти станції»: там показані лише сумісні з грою потоки.",
        "faq_q12": "Кнопка гри «Оновити з Інтернету» стерла мої станції.",
        "faq_a12": "Програма пам'ятає станції, додані через неї (вони жовті у списку). Натисніть «↺ Мої станції» на вкладці «Радіо» — вони повернуться у файл гри.",
        "faq_q13": "Гра вилітає. Як знайти причину?",
        "faq_a13": "«Діагностика» → «Збої»: програма розбирає game.crash.txt, пояснює код помилки й показує програми, які були завантажені в гру під час збою (оверлеї, RivaTuner, Discord, ReShade тощо). Вимикайте їх по одному. 🔥 — найсильніша ознака: модуль був у стеку викликів. Посилання 🔎 шукають рішення в Google, на форумі SCS і в Steam, а «📋 Копіювати звіт» готує текст без особистих шляхів для форуму.",
        "faq_q14": "Проблеми з World of Trucks, конвоєм або підключенням.",
        "faq_a14": "«Діагностика» → «Мережа»: розбір net.log (тайм-аути, обриви з'єднання, DNS) і кнопка «🔌 Перевірити мережу» для перевірки серверів SCS і Steam. Якщо World of Trucks пише «Access denied», вийдіть з акаунта WoT у грі й увійдіть знову.",
        "faq_q15": "Як звільнити місце на диску?",
        "faq_a15": "Вкладка «Очищення»: кеш гри, старі автоматичні бекапи профілів steam_profiles(версія).bak (вони переміщуються в Кошик) і скриншоти. Найбільше місця зазвичай займають невикористані моди — див. «Моди» → «Невикористані».",
        "faq_q16": "Windows або антивірус попереджає про програму.",
        "faq_a16": "Програма не має платного цифрового підпису, а такі exe-файли SmartScreen і деякі антивіруси часто позначають як невідомі. Якщо ви завантажили програму з офіційного джерела автора, у вікні SmartScreen натисніть «Докладніше» → «Виконати в будь-якому разі». Не запускайте копії з невідомих сайтів.",
        "faq_q17": "Чи потрібно встановлювати програму? Де вона зберігає свої дані?",
        "faq_a17": "Встановлювати не потрібно — програма портативна. Її налаштування лежать у settings.json поруч з exe, пресети керування — у папці гри в Controls_Presets, а кеш назв Workshop-модів і ваші радіостанції — у %LOCALAPPDATA%\\TruckControlsManager. Файли гри змінюються лише тоді, коли ви натискаєте кнопку збереження чи застосування.",
        "close": "Закрити",
        "btn_radio_search": "🌐 Знайти станції",
        "rsearch_title": "Пошук радіостанцій в Інтернеті (каталог Radio Browser)",
        "rsearch_name_ph": "Назва станції (напр. Kiss FM, Хіт FM)...",
        "rsearch_tag_ph": "Жанр (rock, pop, jazz, news)...",
        "rsearch_any_country": "Усі країни",
        "rsearch_btn": "🔍 Шукати",
        "rsearch_hint": "Пошук у відкритому каталозі radio-browser.info (понад 50 000 станцій). Показано лише прямі потоки MP3/AAC, які програє гра. Подвійний клік або «Додати вибрані» — у список гри; «⚡ Найкращі робочі» сам перевірить найпопулярніші нові станції й додасть лише ті, що справді грають.",
        "rsearch_searching": "Шукаю в каталозі...",
        "rsearch_found": "Знайдено станцій, сумісних з грою: {}. Сірим — ті, що вже є у вашому списку.",
        "rsearch_nothing": "Нічого не знайдено — спробуйте іншу назву, жанр або «Усі країни».",
        "rsearch_error": "Каталог недоступний: {}",
        "rsearch_col_name": "Назва",
        "rsearch_col_genre": "Жанр",
        "rsearch_col_country": "Країна",
        "rsearch_col_codec": "Формат",
        "rsearch_col_bitrate": "kbps",
        "rsearch_col_votes": "Голоси",
        "rsearch_col_status": "Стан",
        "rsearch_in_game": "✓ уже в грі",
        "rsearch_add_selected": "➕ Додати вибрані",
        "rsearch_auto_label": "Автододати:",
        "rsearch_auto_btn": "⚡ Найкращі робочі",
        "rsearch_auto_tip": "Бере найпопулярніші станції з результатів, яких ще немає в грі, перевіряє, чи потік справді грає, і додає вказану кількість робочих.",
        "rsearch_need_results": "Спершу виконайте пошук.",
        "rsearch_all_in_game": "Усі знайдені станції вже є в грі або не відповідають.",
        "rsearch_auto_running": "Перевіряю потоки й добираю {} робочих станцій...",
        "rsearch_auto_done": "Додано станцій: {}  ·  перевірено: {}, не відповіли: {}",
        "rsearch_added": "Додано станцій у гру: {}",
        "btn_mods_sort": "🧮 Автосортування",
        "btn_mods_sort_undo": "↶ Скасувати",
        "modsort_tip": "Упорядкувати моди профілю за правилами: інтерфейс, графіка, звук і дрібні зміни — вгорі; скіни, тюнінг, тягачі, причепи, трафік — посередині; карти й моди-залежності — внизу. Моди однієї серії лишаються разом, ваш порядок у межах групи зберігається. Перед записом — попередній перегляд і бекап.",
        "modsort_game_running": "Закрийте гру: при виході вона перезаписує profile.sii, і новий порядок модів загубиться.",
        "modsort_too_few": "У профілі менше двох увімкнених модів — сортувати нічого.",
        "modsort_analyzing": "Аналізую моди (manifest.sii, теги Workshop)...",
        "modsort_already": "Порядок модів уже відповідає правилам — змінювати нічого.",
        "modsort_title": "Автосортування модів — попередній перегляд",
        "modsort_preview_head": "Новий порядок (згори — найвищий пріоритет, як у менеджері модів гри):",
        "modsort_was": "(було {})",
        "modsort_legend": "↑/↓ — мод переміститься вище/нижче  ·  🔗 — серія модів, лишається разом  ·  ? — категорію вгадано за назвою або не визначено (невідомі стоять у групі «Інші зміни»)",
        "modsort_summary": "Модів: {}  ·  переміститься: {}  ·  серій, що лишаються разом: {}  ·  без відомої категорії: {}\nПравила загальні: якщо автор мода вимагає особливого порядку (карти, комбо-збірки) — дотримуйтеся його інструкції. Бекап profile.sii створюється автоматично, а «↶ Скасувати» поверне попередній порядок.",
        "modsort_apply": "✅ Застосувати порядок",
        "modsort_changed": "Список модів у profile.sii змінився — запустіть автосортування ще раз.",
        "modsort_done": "Порядок модів оновлено",
        "modsort_done_msg": "Порядок модів оновлено. Запустіть гру й перевірте менеджер модів. Якщо щось не так — кнопка «↶ Скасувати» у вкладці «Моди» поверне попередній порядок.",
        "modsort_undo_confirm": "Повернути попередній порядок модів?\n\n{}",
        "modsort_undone": "Попередній порядок модів повернуто",
        "modsort_tier_ui": "Інтерфейс, навігація, мапа",
        "modsort_tier_graphics": "Графіка та погода",
        "modsort_tier_sound": "Звук",
        "modsort_tier_physics": "Фізика",
        "modsort_tier_other": "Інші зміни",
        "modsort_tier_paint": "Скіни та розфарбування",
        "modsort_tier_parts": "Тюнінг, аксесуари, інтер'єри",
        "modsort_tier_truck": "Тягачі",
        "modsort_tier_trailer": "Причепи та вантажі",
        "modsort_tier_traffic": "AI-трафік",
        "modsort_tier_models": "Моделі та моди-залежності",
        "modsort_tier_map": "Карти (завжди внизу)",
        "radio_add_title": "Нова радіостанція",
        "radio_field_url": "Адреса потоку:",
        "radio_field_name": "Назва:",
        "radio_field_genre": "Жанр:",
        "radio_field_lang": "Мова (код):",
        "radio_field_bitrate": "Бітрейт, kbps:",
        "radio_check_btn": "🔎 Перевірити",
        "radio_checking_one": "Перевірка потоку...",
        "radio_bad_url": "Вкажіть адресу, що починається з http:// або https://",
        "radio_bad_name": "Вкажіть назву станції",
        "radio_duplicate": "Станція з такою адресою вже є у списку.",
        "radio_added": "Станцію «{}» додано",
        "radio_pick": "Виберіть станції у списку (Ctrl/Shift — кілька).",
        "radio_delete_confirm": "Видалити станцій: {}?\n\n{}",
        "radio_deleted": "Видалено станцій: {}",
        "radio_nothing_to_reapply": "Усі ваші станції вже є у файлі гри.",
        "radio_reapplied": "Повернуто ваших станцій: {}",
        "radio_checking": "Перевірка станцій: {} / {}...",
        "radio_check_done": "Перевірку завершено: працюють {}, не відповідають {}",
        "cancel": "Скасувати",
        "tab_logs": "🔍 Діагностика",
        "tab_clean_mods": "🧹 Очищення",
        "tab_settings": "📁 Налаштування",
        "game_select": "Виберіть гру:",
        "current_game_path": "Папка гри:",
        "btn_change_path": "📂 Змінити папку...",
        "btn_open_game_folder": "↗ Відкрити в Провіднику",
        "path_status_found": "Знайдено профілів: {}",
        "path_status_not_found": "Папку не знайдено або відсутні профілі!",
        "game_proc_running": "🔴 Гра наразі запущена! Зміна налаштувань не рекомендується",
        "game_proc_closed": "🟢 Гра закрита (безпечно)",
        "btn_launch_game": "🚀 Запустити гру",
        "profile_label": "Профіль:",
        "profile_select_prompt": "Оберіть профіль...",
        "profile_hint": "Пресет копіює: controls.sii • config.cfg • config_local.cfg • global_controls.sii",
        "presets_label": "Збережені пресети керування:",
        "presets_empty": "Пресетів ще немає. Збережіть свій перший пресет нижче!",
        "btn_apply": "✅  ЗАСТОСУВАТИ ПРЕСЕТ",
        "btn_save": "💾 Зберегти як пресет",
        "btn_backup": "🛡 Швидкий бекап",
        "btn_open_presets": "📂 Папка пресетів",
        "btn_import_preset": "📥 Імпорт пресету",
        "export_preset_title": "Експорт пресету",
        "import_preset_title": "Імпорт пресету",
        "preset_exported": "Пресет «{}» експортовано:\n{}\n\nЦей файл можна передати друзям — вони імпортують його кнопкою «Імпорт пресету».",
        "preset_imported": "Пресет «{}» імпортовано!",
        "preset_import_bad": "Це не архів пресету: у ньому немає файлів керування ({}).",
        "preset_import_too_big": "Файл «{}» в архіві завеликий — імпорт скасовано.",
        "preset_import_other_game": "Цей пресет створено для {}, а зараз вибрано {}.\n\nКерування цих ігор може відрізнятися. Імпортувати все одно?",
        "btn_delete": "🗑 Видалити",
        "btn_refresh": "🔄 Оновити",
        "preset_name_title": "Новий пресет",
        "preset_name_prompt": "Введіть назву пресету (наприклад: Кермо G29, Геймпад, Клавіатура):",
        "bad_preset_name": "Некоректна назва пресету.",
        "preset_exists": "Пресет «{}» вже існує. Перезаписати?",
        "preset_saved": "Пресет «{}» успішно збережено!",
        "preset_apply_confirm": "Застосувати пресет «{}» до профілю «{}»?\n\n(Перед заміною автоматично створиться резервна копія)",
        "preset_applied": "Пресет «{}» успішно застосовано!",
        "preset_delete_confirm": "Видалити пресет «{}»?",
        "preset_deleted": "Пресет «{}» видалено.",
        "pick_profile": "Будь ласка, оберіть профіль зі списку!",
        "pick_preset": "Будь ласка, оберіть пресет зі списку!",
        "no_control_files": "У вибраному профілі не знайдено файлів керування!",
        "manual_backup_ok": "Резервну копію створено:\n{}",
        "warning_game_running": "⚠️ УВАГА: Гра наразі ЗАПУЩЕНА!\n\nЯкщо змінити файли зараз, гра перезапише їх своїми даними при закритті.\n\nПродовжити все одно?",
        "tweaks_title": "Геймплейні налаштування (config.cfg)",
        "tweak_card_console": "Консоль розробника та HUD",
        "tweak_minicon": "Міні-консоль на екрані (g_minicon: показує FPS і координати)",
        "tweak_card_traffic": "Трафік, Вільна камера та FPS",
        "traffic_label": "Густота трафіку (g_traffic):",
        "flyspeed_label": "Швидкість вільної камери (g_flyspeed):",
        "fps_label": "Ліміт частоти кадрів (t_limit_fps):",
        "tweak_card_rules": "Геймплейні правила та помічники",
        "tweak_police": "Штрафи поліції (g_police): вимкніть, щоб їздити без штрафів",
        "tweak_fatigue": "Втома водія (g_fatigue): вимкніть, щоб не зупинятися на сон",
        "tweak_steering_wheel": "Віртуальне кермо в кабіні (r_steering_wheel): приховати для керм Logitech/Moza",
        "btn_reset_economy": "🔄 Скинути біржу вантажів",
        "btn_reset_economy_hint": "Лікує «Немає доступних вантажів» після оновлення або видалення модів",
        "economy_reset_done": "g_force_economy_reset = 1 записано у профіль.\n\nЗапустіть гру й завантажте профіль — ринок вантажів згенерується заново. Якщо гра вже запущена: у вільній їзді відкрийте й закрийте менеджер профілів.\n\nПісля оновлення ринку натисніть кнопку ще раз, щоб повернути значення в 0.",
        "economy_reset_undone": "g_force_economy_reset повернуто в 0.",
        "economy_reset_pending": "⚠ Скидання активне (g_force_economy_reset = 1). Після оновлення ринку поверніть у 0",
        "btn_reset_economy_undo": "↩ Повернути скидання ринку в 0",
        "tweak_card_physics": "Фізика кабіни, тягача та причепа",
        "tweak_cam_physics": "Фізика камери водія (g_cam_physics_value):",
        "tweak_cabin_stiffness": "Жорсткість підвіски кабіни (g_cabin_suspension_stiffness):",
        "tweak_truck_stability": "Стабільність тягача (g_truck_stability):",
        "tweak_trailer_stability": "Стабільність причепа (g_trailer_stability):",
        "tweak_brake_intensity": "Ефективність гальм (g_brake_intensity):",
        "tweak_card_graphics": "Оптимізація графіки та FPS",
        "tweak_pedestrian": "Пішоходи на тротуарах (g_pedestrian: вимкніть для +FPS у містах)",
        "tweak_cloud_shadows": "Тіні від хмар (r_cloud_shadows: вимкніть для +FPS на слабких відеокартах)",
        "btn_save_tweaks": "💾 Зберегти всі налаштування",
        "btn_restore_default_tweaks": "↩️ Скинути до значень за замовчуванням (Default)",
        "restore_defaults_confirm": "Скинути всі твіки до стандартних рекомендованих параметрів гри?",
        "tweaks_saved": "Налаштування успішно збережено (config.cfg гри та файли профілю)!",
        "tweaks_profile_skipped": "Профіль не вибрано — налаштування профілю (поліція, втома, кермо, фізика, гальма) не збережено.",
        "tweak_no_profile": "профіль не вибрано",
        "console_title": "Консоль розробника та вільна камера",
        "console_status_label": "Статус консолі:",
        "console_status_on": "🟢 УВІМКНЕНО (g_developer=1, g_console=1)",
        "console_status_off": "⚪ ВИМКНЕНО",
        "console_done_on": "Консоль розробника увімкнено! У грі відкривається клавішею ~",
        "console_done_off": "Консоль розробника вимкнено.",
        "btn_console_on": "🟢 Увімкнути консоль + Free Cam",
        "btn_console_off": "🔴 Вимкнути консоль + Free Cam",
        "cheat_sheet_title": "📖 Детальна шпаргалка гарячих клавіш та команд консолі",
        "console_cheat_sheet": (
            "Гарячі клавіші:\n"
            "  •  ~ (тильда)  — відкрити / закрити консоль\n"
            "  •  0 (нуль)  — перехід у режим вільної камери (Free Cam)\n"
            "  •  Num 8 / 4 / 5 / 6  — політ камери (вперед / вліво / назад / вправо)\n"
            "  •  Num 9 / 3  — підйом вище / опуск нижче\n"
            "  •  Коліщатко миші  — регулювання швидкості вільної камери на льоту\n"
            "  •  Ctrl + F9  — телепортувати тягач із причепом до позиції камери\n"
            "  •  F9  — телепортувати лише тягач (у версіях гри до 1.41)\n\n"
            "Популярні команди консолі (~):\n"
            "  •  goto <місто>  — переліт камери (напр: goto kyiv, goto berlin, goto warszawa)\n"
            "  •  g_set_time <0-24>  — встановити точний час (напр: g_set_time 12 — день, g_set_time 0 — ніч)\n"
            "  •  g_set_weather 0 / 1  — змінити погоду (0 = сонячно, 1 = дощ)\n"
            "  •  warp 1.0  — швидкість плину часу симулятора (0.8 = slow-mo, 1.5 = прискорено)\n"
            "  •  g_traffic 0..10  — зміна щільності трафіку на льоту прямо під час рейсу\n"
            "  •  r_steering_wheel 0 / 1  — приховати/показати віртуальне кермо"
        ),
        "saves_title": "Менеджер резервних копій сейвів (Save Backups)",
        "stat_experience": "Досвід (XP)",
        "stat_distance": "Пробіг",
        "stat_money": "На рахунку",
        "stat_mods": "Активних модів",
        "stat_cities": "Відвідано міст",
        "stat_explored": "Досліджено карти",
        "stat_slots": "Слотів сейвів",
        "stat_last_save": "Останнє збереження",
        "profile_none": "👤 Профіль не вибрано",
        "profile_sub": "{}  ·  улюблена марка: {}  ·  створено {}",
        "profile_type_cloud": "Steam Cloud",
        "profile_type_local": "Локальний профіль",
        "profile_unreadable": "не вдалося прочитати profile.sii",
        "saves_desc": "Створюйте повні ZIP-архіви збережень перед оновленням гри або експериментами з модами.",
        "btn_create_save_zip": "📦 Створити ZIP-архів збережень",
        "btn_slot_edit": "✏ Редагувати",
        "editor_title": "Редактор сейву — {}",
        "editor_money": "Гроші на рахунку, {}:",
        "editor_xp": "Досвід (XP):",
        "editor_adr": "ADR (небезпечні вантажі):",
        "editor_adr_class": "клас {}",
        "editor_skill_long_dist": "Далекі рейси (0–6):",
        "editor_skill_heavy": "Важкі вантажі (0–6):",
        "editor_skill_fragile": "Крихкі вантажі (0–6):",
        "editor_skill_urgent": "Термінова доставка (0–6):",
        "editor_skill_mechanical": "Еко-водіння (0–6):",
        "editor_note": "Закрийте гру перед збереженням. Програма змінить лише ці значення, а перед записом автоматично зробить бекап слота (архів «_pre_edit_…»). Сейв буде записано в розшифрованому вигляді — гра такі відкриває; якщо ні, відновіть бекап на вкладці «ZIP-архіви». Гроші в меню завантаження оновляться після наступного збереження в грі.",
        "editor_save": "💾 Зберегти зміни",
        "editor_bad_number": "Гроші та досвід мають бути цілими числами.",
        "editor_out_of_range": "Значення поза допустимими межами ({} … {}).",
        "editor_cannot_read": "Не вдалося прочитати сейв:\n{}\n\nРедагування недоступне для цього слота.",
        "editor_game_running": "Закрийте гру перед редагуванням сейву — інакше гра перезапише зміни.",
        "editor_confirm": "Записати зміни у слот «{}»?\n\n{}\n\nПеред записом буде створено бекап слота.",
        "editor_field_money_account": "Гроші",
        "editor_field_experience_points": "Досвід",
        "editor_field_adr": "ADR (маска)",
        "editor_field_long_dist": "Далекі рейси",
        "editor_field_heavy": "Важкі вантажі",
        "editor_field_fragile": "Крихкі вантажі",
        "editor_field_urgent": "Термінова доставка",
        "editor_field_mechanical": "Еко-водіння",
        "editor_done": "Зміни збережено.\n\nЗапустіть гру й завантажте цей слот. Якщо щось не так — відновіть архів «{}» на вкладці «ZIP-архіви».",
        "editor_done_status": "Сейв «{}» відредаговано",
        "saves_view_slots": "Слоти сейвів",
        "saves_view_zip": "ZIP-архіви",
        "saves_slots_hint": "Найновіші згори · ⚠ — сейв залежить від мода, якого немає на диску",
        "slots_empty": "У цьому профілі ще немає збережень.",
        "slot_autosave": "Автозбереження",
        "slot_autosave_drive": "Автозбереження в дорозі",
        "slot_autosave_job": "Автозбереження (рейс)",
        "slot_mp_backup": "Бекап мультиплеєра",
        "slot_quicksave": "Швидке збереження",
        "slot_mods": "модів: {}",
        "slot_missing": "Бракує модів ({}): {} — сейв може не завантажитися",
        "btn_slot_backup": "📦 Бекап",
        "slot_backup_done": "Бекап слота «{}» створено",
        "slot_restore_confirm": "Відновити слот «{1}» з архіву «{0}»?\n\nБуде замінено лише цей слот (перед цим автоматично створиться його бекап).",
        "archive_kind_slot": "слот: {}",
        "archive_kind_full": "уся папка save",
        "btn_restore_save": "⚡ Відновити з вибраного архіву",
        "btn_delete_save_zip": "🗑 Видалити архів",
        "btn_open_save_folder": "📂 Папка архівів",
        "save_zip_created": "Архів сейвів успішно створено:\n{}\nРозмір: {}",
        "save_zip_restore_confirm": "Відновити сейви з архіву «{}»?\n\nПоточні сейви буде замінено (перед цим автоматично створиться безпечний бекап).",
        "cloud_restore_note": "\n\nПрофіль синхронізується зі Steam Cloud. Якщо під час запуску гри Steam покаже конфлікт хмарних файлів — оберіть локальні файли (на цьому комп'ютері).",
        "save_zip_restored": "Сейви успішно відновлено з архіву!",
        "no_save_folder": "Папку збережень (save) у цьому профілі не знайдено.",
        "log_title": "Діагностика: лог гри, збої та мережа",
        "log_stats": "Знайдено в лозі: Помилок <ERROR>: {}  •  Попереджень <WARNING>: {}",
        "log_search_placeholder": "Пошук / фільтр помилок за назвою моду чи текстури...",
        "btn_refresh_log": "🔄 Оновити",
        "btn_open_log_file": "📄 game.log.txt",
        "btn_clear_log_file": "🧹 Очистити старий лог",
        "log_view_crashes": "Збої",
        "log_view_network": "Мережа",
        "btn_net_check": "🔌 Перевірити мережу",
        "btn_copy_report": "📋 Копіювати звіт",
        "report_tip": "Звіт без особистих шляхів — для форуму SCS, Steam або Discord",
        "search_scs_forum": "🔎 Форум SCS",
        "crash_head": "💥 Збоїв: {0}  (з {1} по {2})  ·  у поточній версії {4}: {3}",
        "crash_suspects_head": "⚠ Підозрювані модулі (оверлеї, хуки, сторонні DLL)",
        "crash_suspects_hint": "Вони були завантажені в гру під час збоїв. 🔥 — модуль був у стеку викликів, це найімовірніша причина. Спробуйте вимикати їх по одному.",
        "crash_in_n": "у {} із {} збоїв",
        "crash_in_stack": "🔥 у стеку викликів",
        "crash_recent_head": "🕑 Останні збої",
        "crash_low_mem": "⚠ Під час збою було лише {} МБ вільної пам'яті — закрийте зайві програми або зменшіть кількість модів.",
        "crash_stack_mods": "Сторонні модулі у стеку: {}",
        "crash_old_version": "Стара версія гри — після оновлення збій може бути неактуальним.",
        "km_rtss": "RivaTuner / MSI Afterburner (оверлей)",
        "km_rtss_tip": "Вимкніть оверлей RTSS для гри або додайте eurotrucks2.exe/amtrucks.exe у виключення з Application detection level = None.",
        "km_discord": "Оверлей Discord",
        "km_discord_tip": "Discord → Налаштування → Оверлей гри → вимкніть для цієї гри.",
        "km_steam_overlay": "Оверлей Steam",
        "km_steam_overlay_tip": "Зазвичай безпечний, але при частих вильотах вимкніть: Steam → Властивості гри → «Увімкнути оверлей Steam».",
        "km_asus": "ASUS Armoury Crate / Aura (підсвітка)",
        "km_asus_tip": "Служби підсвітки ASUS впроваджуються в ігри. Якщо вильоти тривають — вимкніть Aura Sync/«ігрову підсвітку» або закрийте Armoury Crate перед грою.",
        "km_headtrack": "opentrack / TrackIR (трекінг голови)",
        "km_headtrack_tip": "Оновіть opentrack/TrackIR або вимкніть трекінг голови для перевірки.",
        "km_obs": "OBS (захоплення гри)",
        "km_obs_tip": "Спробуйте захоплення вікна/екрана замість «Захоплення гри».",
        "km_overwolf": "Overwolf",
        "km_overwolf_tip": "Вимкніть оверлей Overwolf для цієї гри.",
        "km_reshade": "ReShade",
        "km_reshade_tip": "Оновіть або тимчасово видаліть ReShade з папки гри.",
        "km_nvidia": "Драйвер NVIDIA",
        "km_nvidia_tip": "Оновіть драйвер відеокарти (або виконайте чисте встановлення).",
        "km_amd": "Драйвер AMD",
        "km_amd_tip": "Оновіть драйвер відеокарти (або виконайте чисте встановлення).",
        "km_injector": "Стороння DLL у папці гри (bin\\win_x64)",
        "km_injector_tip": "Такі файли кладуть ReShade, ASI-лоадери, DLSS/FSR-підміни. Для перевірки тимчасово перемістіть DLL з папки гри (Steam → «Перевірити цілісність» поверне лише файли гри).",
        "exc_access": "ACCESS_VIOLATION — звернення до недійсної пам'яті: найчастіше несумісний мод, оверлей/хук, нестабільний розгін або драйвер.",
        "exc_noncont": "NONCONTINUABLE_EXCEPTION — критична помилка після попередньої: часто брак пам'яті, проблема мода або стороннього модуля.",
        "exc_stack": "STACK_OVERFLOW — переповнення стека: часто через пошкоджений мод або сейв.",
        "exc_overrun": "STACK_BUFFER_OVERRUN — захист від пошкодження пам'яті: оверлеї/хуки або пошкоджені файли гри.",
        "exc_illegal": "ILLEGAL_INSTRUCTION — процесор не виконав інструкцію: нестабільний розгін CPU або пошкоджені файли.",
        "exc_cpp": "C++ exception — внутрішня помилка гри; подивіться game.log.txt перед збоєм.",
        "exc_break": "BREAKPOINT — гра зупинилась на внутрішній перевірці (assert): часто через мод.",
        "exc_heap": "HEAP_CORRUPTION — пошкодження пам'яті: моди, оверлеї або нестабільна RAM.",
        "exc_nomem": "NO_MEMORY — не вистачило пам'яті.",
        "exc_other": "Невідомий тип збою — пошукайте код в інтернеті.",
        "net_check_head": "🔌 Перевірка з'єднання зараз",
        "net_checking": "⏳ Перевірка з'єднання...",
        "net_check_good": "Усі сервери доступні — інтернет і DNS працюють.",
        "net_check_bad": "Частина серверів недоступна — перевірте інтернет, DNS, VPN, фаєрвол/антивірус.",
        "net_no_log": "Файл net.log не знайдено.",
        "net_head": "🌐 net.log: ігрових сесій {}, мережевих помилок {}",
        "net_hint": "Помилки з'єднання гри з онлайн-сервісами SCS (World of Trucks, новини, конвой). На одиночну гру вони зазвичай не впливають.",
        "net_no_errors": "✅ Мережевих помилок немає.",
        "net_timeout": "⏱ Тайм-аут з'єднання (10060)",
        "net_timeout_tip": "Сервер не відповів вчасно: нестабільний інтернет, VPN або блокування фаєрволом/провайдером.",
        "net_timeout_query": "connection timeout 10060",
        "net_refused": "⛔ З'єднання відхилено (10061)",
        "net_refused_tip": "Сервер відхилив з'єднання — можливо, він тимчасово недоступний.",
        "net_refused_query": "connection refused 10061",
        "net_reset": "🔌 З'єднання обірвано (10054)",
        "net_reset_tip": "З'єднання розірвала мережа або сервер: нестабільний Wi-Fi/VPN, антивірус із перевіркою трафіку.",
        "net_reset_query": "recv failed 10054",
        "net_unreach": "🚫 Мережа недоступна",
        "net_unreach_tip": "Комп'ютер не мав доступу до мережі в той момент.",
        "net_unreach_query": "network unreachable",
        "net_dns": "🔎 Не вдалося знайти сервер (DNS)",
        "net_dns_tip": "Адресу сервера не вдалося розпізнати: перевірте інтернет або змініть DNS (наприклад, 1.1.1.1 чи 8.8.8.8).",
        "net_dns_query": "online.scssoft.com getaddrinfo failed",
        "net_http": "🌐 Не вдалося підключитися (HTTP)",
        "net_http_tip": "Гра не змогла завантажити дані з сервера SCS — зазвичай наслідок помилок вище.",
        "net_http_query": "Unable to connect online.scssoft.com",
        "net_other": "❔ Інші мережеві помилки",
        "net_other_tip": "Подивіться net.log для подробиць.",
        "net_other_query": "net.log error",
        "wot_denied": "🔑 World of Trucks: доступ заборонено",
        "wot_denied_tip": "Сесія World of Trucks застаріла: у грі відкрийте профіль → World of Trucks і увійдіть знову (або перепривʼяжіть акаунт).",
        "wot_denied_query": "World of Trucks Access denied",
        "wot_notfound": "❓ World of Trucks: не знайдено",
        "wot_notfound_tip": "Сервер не знайшов дані профілю/контракту — часто після зміни профілю чи акаунта; зазвичай нешкідливо.",
        "wot_notfound_query": "World of Trucks LINK ERRO Not found",
        "wot_network": "📡 World of Trucks: мережева помилка",
        "wot_network_tip": "Збій зв'язку з World of Trucks — див. обриви з'єднання вище.",
        "wot_network_query": "World of Trucks network error",
        "wot_unregistered": "📝 World of Trucks: гру не зареєстровано",
        "wot_unregistered_tip": "Гру не прив'язано до акаунта World of Trucks — прив'яжіть її в профілі гри.",
        "wot_unregistered_query": "World of Trucks Game is not registered",
        "wot_other": "🔗 World of Trucks: інша помилка",
        "wot_other_tip": "Подивіться net.log для подробиць.",
        "wot_other_query": "World of Trucks LINK ERRO",
        "report_title": "звіт діагностики",
        "report_game": "Гра",
        "report_mods": "Активних модів у профілі: {}",
        "report_missing": "Відсутні моди: {}",
        "report_outdated": "Моди без версії для гри: {}",
        "report_log": "game.log.txt: помилок {}, попереджень {}; основні джерела:",
        "report_crashes": "Збоїв у game.crash.txt: {}; останні:",
        "report_suspects": "Підозрювані модулі",
        "report_net": "Мережевих помилок у net.log: {}",
        "report_copied": "Звіт скопійовано в буфер обміну — вставте його (Ctrl+V) на форумі чи в Discord.",
        "btn_open_crash_file": "💥 game.crash.txt",
        "log_view_groups": "Групи",
        "log_view_errors": "Помилки",
        "log_view_warnings": "Попередження",
        "log_missing": "Файл game.log.txt ще не створено грою.",
        "log_clean": "🎉 Нічого не знайдено — лог чистий.",
        "log_game_version": "версія гри {}",
        "log_groups_head": "Джерел проблем: {}  (📦 — мод, ⚙ — файл гри або повідомлення рушія)",
        "log_n_errors": "помилок: {}",
        "log_n_warnings": "попереджень: {}",
        "log_shown": "--- Показано {} із {} ---",
        "crash_none": "✅ Звітів про збої (game.crash.txt) немає.",
        "crash_total": "💥 Збоїв у game.crash.txt: {}  ·  {}",
        "crash_last": "Останній: {}  ·  версія {}  ·  {}  ·  модуль: {}",
        "crash_third_party": "⚠ Сторонні модулі у стеку викликів: {} (ReShade, оверлеї, плагіни тощо — спробуйте вимкнути)",
        "crash_outdated": "ℹ Останній збій стався в іншій версії гри (зараз {}), тож, найімовірніше, уже неактуальний.",
        "clean_title": "Очищення кешу гри (Cache)",
        "clean_desc": "Видалення тимчасового кешу допомагає виправити вильоти і глюки після оновлень модів.",
        "clean_current_btn": "🧹 Очистити кеш {}",
        "clean_both_btn": "🧹 Очистити кеш ETS 2 та ATS разом",
        "clean_cache_size": "Розмір кешу: {}",
        "clean_confirm": "Видалити папку cache?\n\n{}",
        "clean_ok": "Кеш успішно очищено!",
        "bak_title": "Автоматичні бекапи гри",
        "bak_desc": "При кожному оновленні гра копіює всі профілі в папку steam_profiles(версія).bak. Старі копії ніхто не видаляє. Програма переміщує їх у Кошик, звідки їх можна відновити.",
        "bak_summary": "Бекапів: {} · {}  —  до видалення: {} · {}",
        "bak_none": "Автоматичних бекапів гри не знайдено.",
        "bak_keep": "Залишити останніх:",
        "bak_clean_btn": "🗑 Перемістити старі в Кошик",
        "bak_confirm": "Перемістити в Кошик {} старих бекапів? Залишаться {} найновіших.\n\n{}",
        "bak_done": "Старі бекапи гри переміщено в Кошик: {}",
        "bak_failed": "Не вдалося перемістити в Кошик (можливо, файли зайняті):\n{}",
        "shots_title": "Скриншоти",
        "shots_info": "Файлів: {}  ·  Розмір: {}\nПапка: {}",
        "shots_open": "📂 Відкрити скриншоти",
        "mods_title": "Папка модифікацій (mod)",
        "mods_info": "Папка: {}\nЗагальний об'єм: {} (файлів модів: {})",
        "btn_open_mods": "📂 Відкрити папку mod",
        "btn_mods_refresh": "🔄 Пересканувати",
        "btn_workshop_subs": "🌐 Підписки Workshop",
        "mods_folder_line": "Папка mod: {}",
        "mods_workshop_line": "Steam Workshop: {}",
        "mods_scanning": "⏳ Сканування модів...",
        "mods_view_order": "Порядок у профілі",
        "mods_view_problems": "Проблеми",
        "mods_view_unused": "Невикористані",
        "mods_search_placeholder": "Пошук мода за назвою...",
        "mods_summary": "У профілі увімкнено: {}  ·  встановлено: {} у mod + {} Workshop ({})\nВідсутні: {}  ·  Без версії для поточної гри: {}  ·  Не використовуються жодним профілем: {} ({})",
        "mods_order_head": "Увімкнені моди профілю: {}  (згори — найвищий пріоритет, як у менеджері модів гри)",
        "mods_note_missing": "немає на диску",
        "mods_note_outdated": "немає версії для {}",
        "mods_no_problems": "✅ Проблем не знайдено: усі увімкнені моди на місці й мають версію для поточної гри.",
        "mods_missing_head": "❌ Увімкнені, але відсутні на диску: {}",
        "mods_missing_hint": "Гра пропустить ці моди, а сейви, що від них залежать, можуть не завантажитися. Перепідпишіться в Workshop або поверніть файл у папку mod.",
        "mods_outdated_head": "⚠ Немає версії для гри {1}: {0}",
        "mods_outdated_hint": "Автор мода ще не оновив його (versions.sii). Такі моди можуть спричиняти помилки або вильоти.",
        "mods_no_unused": "✅ Усі встановлені моди використовуються хоча б одним профілем.",
        "mods_unused_head": "💤 Не увімкнені в жодному профілі цієї гри: {} ({})",
        "mods_unused_hint": "Підписки Workshop можна скасувати кнопкою «Підписки Workshop» — Steam видалить файли сам. Перевірено всі профілі гри.",
        "settings_title": "Налаштування шляхів до ігор",
        "server_card_title": "Сервер конвою (server_config.sii)",
        "server_btn": "⚙ Налаштувати сервер",
        "server_missing": "Файл server_config.sii ще не створено грою:\n{}",
        "server_summary": "Назва: {}  ·  гравців до: {}  ·  пароль: {}  ·  модераторів: {}\n{}\nЦей файл використовує виділений сервер конвою (Dedicated Server). Якщо сервер має свою папку, скопіюйте файл туди.",
        "server_saved": "server_config.sii збережено (бекап створено поруч).",
        "server_title": "Налаштування сервера конвою",
        "server_sec_general": "Загальне",
        "server_sec_traffic": "Трафік",
        "server_sec_rules": "Правила сесії",
        "server_sec_network": "Мережа",
        "server_sec_moderators": "Модератори",
        "server_f_lobby_name": "Назва сесії",
        "server_f_description": "Опис",
        "server_f_welcome_message": "Привітання",
        "server_f_password": "Пароль",
        "server_f_max_players": "Макс. гравців",
        "server_f_timezones": "Часові пояси",
        "server_f_traffic": "Трафік увімкнено",
        "server_f_max_vehicles_total": "Макс. транспорту всього",
        "server_f_max_ai_vehicles_player": "Макс. AI-машин на гравця",
        "server_f_max_ai_vehicles_player_spawn": "AI-машин на гравця при появі",
        "server_f_player_damage": "Гравці можуть пошкоджувати одне одного",
        "server_f_hide_in_company": "Ховати інших гравців на території компаній",
        "server_f_hide_colliding": "Ховати машини, що зіткнулися після телепорту",
        "server_f_force_speed_limiter": "Примусовий обмежувач швидкості",
        "server_f_mods_optioning": "Необов'язкові моди справді необов'язкові",
        "server_f_service_no_collision": "Без зіткнень у зонах сервісу",
        "server_f_in_menu_ghosting": "Без зіткнень, коли гравець у меню (пауза)",
        "server_f_name_tags": "Імена гравців над машинами",
        "server_f_friends_only": "Лише друзі (не для виділеного сервера)",
        "server_f_show_server": "Показувати сервер (не для виділеного сервера)",
        "server_f_connection_virtual_port": "Віртуальний порт підключення",
        "server_f_query_virtual_port": "Віртуальний порт запитів",
        "server_f_connection_dedicated_port": "Фізичний порт підключення",
        "server_f_query_dedicated_port": "Фізичний порт запитів",
        "server_f_server_logon_token": "Logon-токен Steam",
        "server_f_moderator_list": "Steam ID модераторів — по одному в рядку (їх буде автоматично призначено при вході):",
        "server_err_int": "«{}» має бути цілим числом.",
        "server_err_len": "«{}»: щонайбільше {} байт (кирилична літера — 2 байти).",
        "server_err_range": "«{}»: допустимо від {} до {}.",
        "server_err_ids": "Steam ID модераторів мають складатися лише з цифр.",
        "server_err_same_port": "«{}» має відрізнятися від порту підключення.",
        "yes": "так",
        "no": "ні",
        "settings_desc": "Шлях визначається автоматично (параметр -homedir у Steam або «Документи»). Вкажіть папку вручну, якщо дані гри лежать деінде:",
        "path_src_manual": "вибрано вручну",
        "path_src_homedir": "авто: -homedir з параметрів запуску Steam",
        "path_src_documents": "авто: папка «Документи»",
        "reset_paths_done": "Шляхи визначено автоматично:\n\n{}",
        "btn_reset_paths": "↺ Визначити шляхи автоматично",
        "btn_open_logs": "📋 Відкрити папку логів додатку",
        "steam_sales_label": "Знижки Steam:",
        "steam_checking": "Перевірка цін Steam...",
        "steam_none": "Зараз немає знижок у Steam",
        "dlg_error": "Помилка",
        "dlg_warning": "Увага",
        "dlg_confirm": "Підтвердження",
        "dlg_delete": "Видалення",
        "dlg_restore": "Відновлення",
        "dlg_cleanup": "Очищення",
        "dlg_info": "Інформація",
        "dlg_result": "Результат",
        "dlg_cleanup_both": "Очищення обох ігор",
        "dlg_save_editor": "Редактор сейву",
        "dlg_pick_game_folder": "Виберіть папку даних {}",
        "msg_folder_missing": "Папка не існує:\n{}",
        "msg_dir_not_found": "Папку не знайдено:\n{}",
        "fps_unlimited": "Без обмежень",
        "msg_launching": "Запуск {} через Steam...",
        "preset_row_info": "Файлів: {}  •  Змінено: {}",
        "btn_pick": "Обрати",
        "btn_picked": "Обрано ✓",
        "msg_backup_created": "Бекап створено: {}",
        "msg_cfg_missing_short": "🔴 config.cfg не знайдено",
        "msg_cfg_missing": "config.cfg не знайдено:\n{}",
        "saves_empty": "Архівів сейвів ще не створено. Натисніть «📦 Створити ZIP-архів збережень» угорі.",
        "archive_row_info": "Розмір: {}  •  Створено: {}  •  {}",
        "msg_saves_zipped": "Сейви збережено: {}",
        "msg_pick_archive": "Будь ласка, оберіть архів зі списку!",
        "msg_bad_slot_name": "Некоректна назва слота в архіві: {!r}",
        "msg_zip_corrupt": "Пошкоджений файл в архіві: {}",
        "msg_delete_archive": "Ви дійсно хочете видалити архів {}?",
        "msg_log_read_error": "Помилка читання логу: {}",
        "crash_driver": " · драйвер ",
        "msg_cache_clean_already": "Папку cache не знайдено (кеш уже чистий).",
        "msg_clean_both_confirm": "Видалити папки кешу для ETS 2 та ATS разом?",
        "msg_cache_cleaned": "✓ {}: кеш очищено",
        "msg_cache_clean_failed": "✗ {}: помилка ({})",
        "msg_cache_was_clean": "— {}: кеш уже чистий",
        "msg_cache_done": "Очищення кешу завершено",
        "msg_folder_not_game_data": "У вибраній папці не знайдено config.cfg або profiles.\n\nШлях: {}\n\nПідказка: потрібна саме папка даних гри (де лежать profiles і config.cfg), а НЕ папка встановлення гри в Steam.\n\nВикористовувати цю папку?",
        "msg_path_updated": "Шлях оновлено: {}",
        "steam_sales_prefix": "🏷️ Знижки Steam: ",
        "ready_status": "Готово  ·  {}  ·  профілів: {}",
        "ready": "Готово"
    },
    "en": {
        "title": "Truck Controls Manager",
        "subtitle": "Controls, Tweaks, Saves & Optimization Hub for ETS 2 / ATS",
        "tab_controls": "🎮 Controls",
        "tab_tweaks": "🚦 Tweaks & Camera",
        "tab_saves": "💾 Profile & Saves",
        "tab_mods": "📦 Mods",
        "tab_radio": "📻 Radio",
        "radio_title": "In-game Internet Radio (live_streams.sii)",
        "radio_hint": "«🌐 Find Stations» — online search with auto-adding of working ones. Stations added here are remembered separately. If the game's «Update from Internet» button wipes them, press «↺ My Stations». «Check» — selected rows, «Check All» — all shown. Double-click toggles favourite. Click a header to sort.",
        "radio_summary": "Stations: {}  ·  favourites ★: {}  ·  mine (yellow): {}  ·  shown: {}",
        "radio_mine_lost": "⚠ Your stations missing from the game file: {} — press «↺ My Stations».",
        "radio_no_file": "live_streams.sii has not been created yet — start the game at least once.",
        "radio_search_placeholder": "Search by name, genre, language or address...",
        "radio_all_langs": "All languages",
        "radio_col_fav": "★",
        "radio_col_name": "Name",
        "radio_col_genre": "Genre",
        "radio_col_lang": "Lang",
        "radio_col_bitrate": "kbps",
        "radio_col_status": "Check",
        "radio_col_url": "Stream address",
        "btn_radio_add": "➕ Add",
        "btn_radio_fav": "★ Favourite",
        "btn_radio_check": "🔎 Check",
        "btn_radio_check_all": "🔎 Check All",
        "btn_radio_delete": "🗑 Delete",
        "btn_radio_reapply": "↺ My Stations",
        "btn_faq": "❓ FAQ",
        "faq_title": "Frequently Asked Questions",
        "faq_search_ph": "Search questions and answers (e.g. radio, crash, save)...",
        "faq_nothing": "Nothing found — try another word.",
        "faq_go_tab": "→ Open the «{}» tab",
        "faq_footer": "No answer here? Diagnostics → «📋 Copy Report» and describe the problem on the SCS forum.",
        "faq_q1": "The app does not see my game or profiles. What should I do?",
        "faq_a1": "The app finds the game folder itself: in Documents, in the Steam launch option -homedir and in Steam Cloud profiles. If you moved the game data elsewhere, press 📁 next to «Game folder» and pick the folder that contains config.cfg and profiles or steam_profiles. The ↗ button opens this folder in Explorer.",
        "faq_q2": "What does [Steam Cloud] next to a profile mean?",
        "faq_a2": "The profile is synced through Steam Cloud, so its files (config.cfg, profile.sii, saves) live in Steam's userdata\\…\\remote folder. The app finds and edits exactly those files, and Steam uploads the changes on the next game launch. Backups of such files go to Config_Backups in the game folder so they are not uploaded to the cloud.",
        "faq_q3": "I changed a setting but nothing changed in the game.",
        "faq_a3": "The game reads its files on start and overwrites them with its own values on exit. So: close the game → change settings → start the game. The indicator at the top shows whether the game is running. Some tweaks (police, fatigue, physics) are stored in the profile — make sure the right profile is selected.",
        "faq_q4": "Is it safe for my saves? How do I undo everything?",
        "faq_a4": "Before every write the app copies the file (name.bak_date, the last 5 are kept). Before a save is edited, a ZIP archive of the slot «_pre_edit_…» is created, and applying a control preset makes a backup first. To restore a file, rename the .bak copy back or restore the archive in «Profile & Saves» → «ZIP Archives». Before big changes it is worth creating a full ZIP archive of your saves.",
        "faq_q5": "How do I move my controls (wheel, gamepad) to another profile or a friend?",
        "faq_a5": "Controls tab: «💾 Save as Preset» stores controls.sii, config.cfg, config_local.cfg and global_controls.sii. Then pick another profile and apply the preset. A preset can be exported to a file and given to a friend, who imports it with «📥 Import Preset».",
        "faq_q6": "After a game update or removing mods there are no jobs on the freight market.",
        "faq_a6": "Tweaks & Camera tab → «🔄 Reset Freight Market». Start the game and load the profile — the job market is generated again. Then press the button once more to set the value back to 0.",
        "faq_q7": "An edited save does not load.",
        "faq_a7": "The editor writes the save decrypted — the game loads such saves. If something went wrong, restore the «_pre_edit_…» archive in «Profile & Saves» → «ZIP Archives». The money in the load menu updates only after the next in-game save — that is normal.",
        "faq_q8": "What do «Missing» and «No version for the current game» mean for mods?",
        "faq_a8": "«Missing» — the mod is enabled in the profile but its file is not on disk (you unsubscribed in the Workshop or deleted it from the mod folder). The game skips it; disable it in the Mod Manager or install it again. «No version for the current game» — the Workshop author has not released an update for your game version yet; such a mod may not work or may cause crashes.",
        "faq_q9": "How does mod auto-sort work? What if it puts something in the wrong place?",
        "faq_a9": "The app takes each mod's category from its manifest.sii, Steam Workshop tags or name, and orders them: UI, graphics, sound on top; skins, tuning, trucks, trailers in the middle; maps and dependency mods at the bottom. Mods of one series stay together and your order within a group is kept. The rules are general: if a map or combo author requires a specific order, follow it. «↶ Undo» restores the previous order, and the profile.sii backup is kept.",
        "faq_q10": "Does the app delete unused mods?",
        "faq_a10": "No. «Mods» → «Unused» only lists mods that are not enabled in any profile, with their size. Unsubscribe from Workshop mods in Steam («Workshop Subscriptions» button) and delete files from the mod folder yourself.",
        "faq_q11": "I added a radio station but it is not in the game or does not play.",
        "faq_a11": "The game reads the station list on start — restart the game. The game plays direct MP3/AAC streams; links to a web page or a playlist (.m3u, .pls) do not work. Test the station with «🔎 Check». Some stations are only available in their own country. The easiest way is «🌐 Find Stations» — it shows only game-compatible streams.",
        "faq_q12": "The game's «Update from Internet» button wiped my stations.",
        "faq_a12": "The app remembers stations added through it (yellow in the list). Press «↺ My Stations» on the Radio tab — they go back into the game file.",
        "faq_q13": "The game crashes. How do I find the cause?",
        "faq_a13": "Diagnostics → «Crashes»: the app reads game.crash.txt, explains the error code and lists programs that were loaded into the game during the crash (overlays, RivaTuner, Discord, ReShade etc.). Disable them one at a time. 🔥 is the strongest sign: the module was in the call stack. The 🔎 links search Google, the SCS forum and Steam, and «📋 Copy Report» prepares a text without personal paths for the forum.",
        "faq_q14": "Problems with World of Trucks, convoy or connection.",
        "faq_a14": "Diagnostics → «Network»: a breakdown of net.log (timeouts, connection resets, DNS) and the «🔌 Check Network» button that tests the SCS and Steam servers. If World of Trucks says «Access denied», log out of WoT in the game and log in again.",
        "faq_q15": "How do I free disk space?",
        "faq_a15": "Cleanup tab: the game cache, old automatic profile backups steam_profiles(version).bak (moved to the Recycle Bin) and screenshots. Unused mods usually take the most space — see «Mods» → «Unused».",
        "faq_q16": "Windows or my antivirus warns about the app.",
        "faq_a16": "The app has no paid code-signing certificate, and SmartScreen and some antiviruses often flag such exe files as unknown. If you downloaded the app from the author's official source, click «More info» → «Run anyway» in SmartScreen. Do not run copies from unknown sites.",
        "faq_q17": "Does the app need installing? Where does it keep its data?",
        "faq_a17": "No installation needed — the app is portable. Its settings are in settings.json next to the exe, control presets are in Controls_Presets in the game folder, and the Workshop name cache and your radio stations are in %LOCALAPPDATA%\\TruckControlsManager. Game files are changed only when you press a save or apply button.",
        "close": "Close",
        "btn_radio_search": "🌐 Find Stations",
        "rsearch_title": "Find Radio Stations Online (Radio Browser catalogue)",
        "rsearch_name_ph": "Station name (e.g. Kiss FM, BBC Radio)...",
        "rsearch_tag_ph": "Genre (rock, pop, jazz, news)...",
        "rsearch_any_country": "All countries",
        "rsearch_btn": "🔍 Search",
        "rsearch_hint": "Searches the open radio-browser.info catalogue (50,000+ stations). Only direct MP3/AAC streams the game can play are shown. Double-click or «Add Selected» puts a station into the game list; «⚡ Best Working» checks the most popular new stations itself and adds only the ones that really play.",
        "rsearch_searching": "Searching the catalogue...",
        "rsearch_found": "Game-compatible stations found: {}. Grey — already in your list.",
        "rsearch_nothing": "Nothing found — try another name, genre or «All countries».",
        "rsearch_error": "Catalogue unavailable: {}",
        "rsearch_col_name": "Name",
        "rsearch_col_genre": "Genre",
        "rsearch_col_country": "Country",
        "rsearch_col_codec": "Format",
        "rsearch_col_bitrate": "kbps",
        "rsearch_col_votes": "Votes",
        "rsearch_col_status": "Status",
        "rsearch_in_game": "✓ already in game",
        "rsearch_add_selected": "➕ Add Selected",
        "rsearch_auto_label": "Auto-add:",
        "rsearch_auto_btn": "⚡ Best Working",
        "rsearch_auto_tip": "Takes the most popular stations from the results that are not in the game yet, checks that the stream really plays and adds the chosen number of working ones.",
        "rsearch_need_results": "Run a search first.",
        "rsearch_all_in_game": "All found stations are already in the game or do not respond.",
        "rsearch_auto_running": "Checking streams and picking {} working stations...",
        "rsearch_auto_done": "Stations added: {}  ·  checked: {}, not responding: {}",
        "rsearch_added": "Stations added to the game: {}",
        "btn_mods_sort": "🧮 Auto-sort",
        "btn_mods_sort_undo": "↶ Undo",
        "modsort_tip": "Order the profile's mods by the rules: UI, graphics, sound and small tweaks on top; skins, tuning, trucks, trailers, traffic in the middle; maps and dependency mods at the bottom. Mods of one series stay together and your order within a group is kept. Preview and backup before writing.",
        "modsort_game_running": "Close the game: it rewrites profile.sii on exit and the new mod order would be lost.",
        "modsort_too_few": "The profile has fewer than two enabled mods — nothing to sort.",
        "modsort_analyzing": "Analysing mods (manifest.sii, Workshop tags)...",
        "modsort_already": "The mod order already follows the rules — nothing to change.",
        "modsort_title": "Mod Auto-sort — Preview",
        "modsort_preview_head": "New order (top — highest priority, as in the in-game Mod Manager):",
        "modsort_was": "(was {})",
        "modsort_legend": "↑/↓ — mod moves up/down  ·  🔗 — mod series, kept together  ·  ? — category guessed from the name or unknown (unknown ones go to «Other tweaks»)",
        "modsort_summary": "Mods: {}  ·  will move: {}  ·  series kept together: {}  ·  without a known category: {}\nThe rules are general: if a mod author requires a special order (maps, combos), follow their instructions. profile.sii is backed up automatically and «↶ Undo» restores the previous order.",
        "modsort_apply": "✅ Apply Order",
        "modsort_changed": "The mod list in profile.sii has changed — run auto-sort again.",
        "modsort_done": "Mod order updated",
        "modsort_done_msg": "Mod order updated. Start the game and check the Mod Manager. If something is wrong, «↶ Undo» on the Mods tab restores the previous order.",
        "modsort_undo_confirm": "Restore the previous mod order?\n\n{}",
        "modsort_undone": "Previous mod order restored",
        "modsort_tier_ui": "UI, navigation, map screen",
        "modsort_tier_graphics": "Graphics & weather",
        "modsort_tier_sound": "Sound",
        "modsort_tier_physics": "Physics",
        "modsort_tier_other": "Other tweaks",
        "modsort_tier_paint": "Skins & paint jobs",
        "modsort_tier_parts": "Tuning, accessories, interiors",
        "modsort_tier_truck": "Trucks",
        "modsort_tier_trailer": "Trailers & cargo",
        "modsort_tier_traffic": "AI traffic",
        "modsort_tier_models": "Models & dependency mods",
        "modsort_tier_map": "Maps (always at the bottom)",
        "radio_add_title": "New Radio Station",
        "radio_field_url": "Stream address:",
        "radio_field_name": "Name:",
        "radio_field_genre": "Genre:",
        "radio_field_lang": "Language (code):",
        "radio_field_bitrate": "Bitrate, kbps:",
        "radio_check_btn": "🔎 Check",
        "radio_checking_one": "Checking stream...",
        "radio_bad_url": "Enter an address starting with http:// or https://",
        "radio_bad_name": "Enter the station name",
        "radio_duplicate": "A station with this address is already in the list.",
        "radio_added": "Station «{}» added",
        "radio_pick": "Select stations in the list (Ctrl/Shift for several).",
        "radio_delete_confirm": "Delete {} stations?\n\n{}",
        "radio_deleted": "Stations deleted: {}",
        "radio_nothing_to_reapply": "All your stations are already in the game file.",
        "radio_reapplied": "Your stations restored: {}",
        "radio_checking": "Checking stations: {} / {}...",
        "radio_check_done": "Check finished: {} working, {} not responding",
        "cancel": "Cancel",
        "tab_logs": "🔍 Diagnostics",
        "tab_clean_mods": "🧹 Cleanup",
        "tab_settings": "📁 Settings",
        "game_select": "Select Game:",
        "current_game_path": "Game folder:",
        "btn_change_path": "📂 Browse Folder...",
        "btn_open_game_folder": "↗ Explorer",
        "path_status_found": "Profiles found: {}",
        "path_status_not_found": "Folder not found or no profiles present!",
        "game_proc_running": "🔴 Game is currently RUNNING! Modifying files not recommended",
        "game_proc_closed": "🟢 Game is closed (Safe to edit)",
        "btn_launch_game": "🚀 Launch Game",
        "profile_label": "Profile:",
        "profile_select_prompt": "Select a profile...",
        "profile_hint": "Preset includes: controls.sii • config.cfg • config_local.cfg • global_controls.sii",
        "presets_label": "Saved Control Presets:",
        "presets_empty": "No presets yet. Save your current controls below!",
        "btn_apply": "✅  APPLY PRESET",
        "btn_save": "💾 Save as Preset",
        "btn_backup": "🛡 Quick Backup",
        "btn_open_presets": "📂 Presets Folder",
        "btn_import_preset": "📥 Import Preset",
        "export_preset_title": "Export Preset",
        "import_preset_title": "Import Preset",
        "preset_exported": "Preset «{}» exported:\n{}\n\nYou can share this file — others import it with the «Import Preset» button.",
        "preset_imported": "Preset «{}» imported!",
        "preset_import_bad": "This is not a preset archive: it has no control files ({}).",
        "preset_import_too_big": "File «{}» in the archive is too large — import cancelled.",
        "preset_import_other_game": "This preset was made for {}, but {} is selected.\n\nControls of these games may differ. Import anyway?",
        "btn_delete": "🗑 Delete",
        "btn_refresh": "🔄 Refresh",
        "preset_name_title": "New Preset",
        "preset_name_prompt": "Enter preset name (e.g. Wheel G29, Gamepad, Keyboard):",
        "bad_preset_name": "Invalid preset name.",
        "preset_exists": "Preset «{}» already exists. Overwrite?",
        "preset_saved": "Preset «{}» saved successfully!",
        "preset_apply_confirm": "Apply preset «{}» to profile «{}»?\n\n(An automatic backup will be created beforehand)",
        "preset_applied": "Preset «{}» applied successfully!",
        "preset_delete_confirm": "Delete preset «{}»?",
        "preset_deleted": "Preset «{}» deleted.",
        "pick_profile": "Please select a profile first!",
        "pick_preset": "Please select a preset from the list!",
        "no_control_files": "No control files found in selected profile!",
        "manual_backup_ok": "Backup created:\n{}",
        "warning_game_running": "⚠️ WARNING: The game is currently RUNNING!\n\nIf you edit settings now, the game will overwrite them upon exit.\n\nContinue anyway?",
        "tweaks_title": "Gameplay Config Tweaks (config.cfg)",
        "tweak_card_console": "Developer Console & HUD",
        "tweak_minicon": "On-screen Mini-Console (g_minicon: displays FPS and coordinates)",
        "tweak_card_traffic": "Traffic, Free Camera & FPS",
        "traffic_label": "Traffic Density (g_traffic):",
        "flyspeed_label": "Free Camera Speed (g_flyspeed):",
        "fps_label": "FPS Limiter (t_limit_fps):",
        "tweak_card_rules": "Gameplay Rules & Assists",
        "tweak_police": "Police Fines (g_police): disable to drive without fines",
        "tweak_fatigue": "Driver Fatigue (g_fatigue): disable for non-stop driving",
        "tweak_steering_wheel": "Virtual Wheel in Cabin (r_steering_wheel): hide for wheel players",
        "btn_reset_economy": "🔄 Reset Freight Market",
        "btn_reset_economy_hint": "Fixes 'No jobs available' after game or mod changes",
        "economy_reset_done": "g_force_economy_reset = 1 written to the profile.\n\nStart the game and load the profile — the freight market will be regenerated. If the game is already running: while free-roaming, open and close the Profile Manager.\n\nAfter the market refreshes, press the button again to set the value back to 0.",
        "economy_reset_undone": "g_force_economy_reset set back to 0.",
        "economy_reset_pending": "⚠ Reset is active (g_force_economy_reset = 1). Set it back to 0 after the market refreshes",
        "btn_reset_economy_undo": "↩ Set Market Reset Back to 0",
        "tweak_card_physics": "Cabin, Truck & Trailer Physics",
        "tweak_cam_physics": "Cabin Camera Physics (g_cam_physics_value):",
        "tweak_cabin_stiffness": "Cabin Suspension Stiffness (g_cabin_suspension_stiffness):",
        "tweak_truck_stability": "Truck Stability (g_truck_stability):",
        "tweak_trailer_stability": "Trailer Stability (g_trailer_stability):",
        "tweak_brake_intensity": "Brake Intensity (g_brake_intensity):",
        "tweak_card_graphics": "Graphics & FPS Optimization",
        "tweak_pedestrian": "Pedestrians on sidewalks (g_pedestrian: disable for +FPS)",
        "tweak_cloud_shadows": "Cloud Shadows (r_cloud_shadows: disable for +FPS on low-end GPUs)",
        "btn_save_tweaks": "💾 Save All Tweaks",
        "btn_restore_default_tweaks": "↩️ Reset to Defaults",
        "restore_defaults_confirm": "Reset all tweaks to game default recommended values?",
        "tweaks_saved": "Settings saved successfully (game config.cfg and profile files)!",
        "tweaks_profile_skipped": "No profile selected — profile settings (police, fatigue, wheel, physics, brakes) were not saved.",
        "tweak_no_profile": "no profile selected",
        "console_title": "Developer Console & Free Camera",
        "console_status_label": "Console Status:",
        "console_status_on": "🟢 ENABLED (g_developer=1, g_console=1)",
        "console_status_off": "⚪ DISABLED",
        "console_done_on": "Developer console enabled! Open it in game with the ~ key",
        "console_done_off": "Developer console disabled.",
        "btn_console_on": "🟢 Enable Console + Free Cam",
        "btn_console_off": "🔴 Disable Console + Free Cam",
        "cheat_sheet_title": "📖 Detailed In-Game Cheat Sheet & Console Commands",
        "console_cheat_sheet": (
            "Hotkeys:\n"
            "  •  ~ (tilde)  — open / close developer console\n"
            "  •  0 (zero)  — enter Free Camera mode\n"
            "  •  Num 8 / 4 / 5 / 6  — camera movement (forward / left / backward / right)\n"
            "  •  Num 9 / 3  — raise / lower camera height\n"
            "  •  Mouse Scroll Wheel  — adjust camera fly speed on the fly\n"
            "  •  Ctrl + F9  — teleport truck and trailer to camera position\n"
            "  •  F9  — teleport truck only (versions prior to 1.41)\n\n"
            "Popular Console Commands (~):\n"
            "  •  goto <city>  — fly camera to city (e.g., goto kyiv, goto warszawa, goto berlin)\n"
            "  •  g_set_time <0-24>  — set time of day (e.g., g_set_time 12 for noon, g_set_time 0 for midnight)\n"
            "  •  g_set_weather 0 / 1  — toggle weather (0 = sunny, 1 = rainy)\n"
            "  •  warp 1.0  — simulation time speed (0.8 = slow-motion, 1.5 = faster)\n"
            "  •  g_traffic 0..10  — adjust traffic density in real-time\n"
            "  •  r_steering_wheel 0 / 1  — toggle virtual steering wheel in cabin"
        ),
        "saves_title": "Savegame Backup Manager",
        "stat_experience": "Experience (XP)",
        "stat_distance": "Distance driven",
        "stat_money": "Bank balance",
        "stat_mods": "Active mods",
        "stat_cities": "Cities visited",
        "stat_explored": "Map explored",
        "stat_slots": "Save slots",
        "stat_last_save": "Last saved",
        "profile_none": "👤 No profile selected",
        "profile_sub": "{}  ·  favourite brand: {}  ·  created {}",
        "profile_type_cloud": "Steam Cloud",
        "profile_type_local": "Local profile",
        "profile_unreadable": "could not read profile.sii",
        "saves_desc": "Create full ZIP archives of your saves before updates or mod testing.",
        "btn_create_save_zip": "📦 Create Save ZIP Backup",
        "btn_slot_edit": "✏ Edit",
        "editor_title": "Save Editor — {}",
        "editor_money": "Bank balance, {}:",
        "editor_xp": "Experience (XP):",
        "editor_adr": "ADR (dangerous goods):",
        "editor_adr_class": "class {}",
        "editor_skill_long_dist": "Long distance (0–6):",
        "editor_skill_heavy": "Heavy cargo (0–6):",
        "editor_skill_fragile": "Fragile cargo (0–6):",
        "editor_skill_urgent": "Just-in-time delivery (0–6):",
        "editor_skill_mechanical": "Ecodriving (0–6):",
        "editor_note": "Close the game before saving. Only these values are changed, and the slot is backed up automatically first (archive «_pre_edit_…»). The save is written decrypted — the game loads such saves; if it does not, restore the backup on the «ZIP Archives» tab. The money shown in the load menu updates after the next in-game save.",
        "editor_save": "💾 Save Changes",
        "editor_bad_number": "Money and experience must be whole numbers.",
        "editor_out_of_range": "Value out of the allowed range ({} … {}).",
        "editor_cannot_read": "Could not read the save:\n{}\n\nEditing is not available for this slot.",
        "editor_game_running": "Close the game before editing the save — otherwise it will overwrite the changes.",
        "editor_confirm": "Write changes to slot «{}»?\n\n{}\n\nThe slot is backed up first.",
        "editor_field_money_account": "Money",
        "editor_field_experience_points": "Experience",
        "editor_field_adr": "ADR (mask)",
        "editor_field_long_dist": "Long distance",
        "editor_field_heavy": "Heavy cargo",
        "editor_field_fragile": "Fragile cargo",
        "editor_field_urgent": "Just-in-time",
        "editor_field_mechanical": "Ecodriving",
        "editor_done": "Changes saved.\n\nStart the game and load this slot. If anything is wrong, restore the archive «{}» on the «ZIP Archives» tab.",
        "editor_done_status": "Save «{}» edited",
        "saves_view_slots": "Save Slots",
        "saves_view_zip": "ZIP Archives",
        "saves_slots_hint": "Newest first · ⚠ — the save depends on a mod that is missing on disk",
        "slots_empty": "This profile has no saves yet.",
        "slot_autosave": "Autosave",
        "slot_autosave_drive": "Autosave (driving)",
        "slot_autosave_job": "Autosave (job)",
        "slot_mp_backup": "Multiplayer backup",
        "slot_quicksave": "Quicksave",
        "slot_mods": "mods: {}",
        "slot_missing": "Missing mods ({}): {} — the save may fail to load",
        "btn_slot_backup": "📦 Backup",
        "slot_backup_done": "Backup of slot «{}» created",
        "slot_restore_confirm": "Restore slot «{1}» from archive «{0}»?\n\nOnly this slot will be replaced (a backup of it is created first).",
        "archive_kind_slot": "slot: {}",
        "archive_kind_full": "whole save folder",
        "btn_restore_save": "⚡ Restore Selected Backup",
        "btn_delete_save_zip": "🗑 Delete Backup",
        "btn_open_save_folder": "📂 Open Backups Folder",
        "save_zip_created": "Save archive created successfully:\n{}\nSize: {}",
        "save_zip_restore_confirm": "Restore saves from archive «{}»?\n\nCurrent saves will be replaced (a safety backup will be created first).",
        "cloud_restore_note": "\n\nThis profile syncs with Steam Cloud. If Steam reports a cloud file conflict when the game starts, choose the local files (on this PC).",
        "save_zip_restored": "Saves restored successfully!",
        "no_save_folder": "Save directory not found in this profile.",
        "log_title": "Diagnostics: game log, crashes and network",
        "log_stats": "Found in log: Errors <ERROR>: {}  •  Warnings <WARNING>: {}",
        "log_search_placeholder": "Filter errors by mod name or keyword...",
        "btn_refresh_log": "🔄 Refresh",
        "btn_open_log_file": "📄 game.log.txt",
        "btn_clear_log_file": "🧹 Clear Old Log",
        "log_view_crashes": "Crashes",
        "log_view_network": "Network",
        "btn_net_check": "🔌 Check Network",
        "btn_copy_report": "📋 Copy Report",
        "report_tip": "Report without personal paths — for the SCS forum, Steam or Discord",
        "search_scs_forum": "🔎 SCS Forum",
        "crash_head": "💥 Crashes: {0}  (from {1} to {2})  ·  in current version {4}: {3}",
        "crash_suspects_head": "⚠ Suspect modules (overlays, hooks, third-party DLLs)",
        "crash_suspects_hint": "They were loaded into the game during crashes. 🔥 — the module was in the call stack, the most likely cause. Try disabling them one at a time.",
        "crash_in_n": "in {} of {} crashes",
        "crash_in_stack": "🔥 in the call stack",
        "crash_recent_head": "🕑 Recent crashes",
        "crash_low_mem": "⚠ Only {} MB of free memory at the time of the crash — close other programs or use fewer mods.",
        "crash_stack_mods": "Third-party modules in the stack: {}",
        "crash_old_version": "Old game version — the crash may be irrelevant after the update.",
        "km_rtss": "RivaTuner / MSI Afterburner (overlay)",
        "km_rtss_tip": "Disable the RTSS overlay for the game, or add eurotrucks2.exe/amtrucks.exe with Application detection level = None.",
        "km_discord": "Discord overlay",
        "km_discord_tip": "Discord → Settings → Game Overlay → disable it for this game.",
        "km_steam_overlay": "Steam overlay",
        "km_steam_overlay_tip": "Usually safe, but disable it if crashes are frequent: Steam → game Properties → «Enable the Steam Overlay».",
        "km_asus": "ASUS Armoury Crate / Aura (lighting)",
        "km_asus_tip": "ASUS lighting services inject into games. If crashes persist, disable Aura Sync/game lighting or close Armoury Crate before playing.",
        "km_headtrack": "opentrack / TrackIR (head tracking)",
        "km_headtrack_tip": "Update opentrack/TrackIR or disable head tracking to test.",
        "km_obs": "OBS (game capture)",
        "km_obs_tip": "Try Window/Display capture instead of Game Capture.",
        "km_overwolf": "Overwolf",
        "km_overwolf_tip": "Disable the Overwolf overlay for this game.",
        "km_reshade": "ReShade",
        "km_reshade_tip": "Update or temporarily remove ReShade from the game folder.",
        "km_nvidia": "NVIDIA driver",
        "km_nvidia_tip": "Update the graphics driver (or do a clean install).",
        "km_amd": "AMD driver",
        "km_amd_tip": "Update the graphics driver (or do a clean install).",
        "km_injector": "Third-party DLL in the game folder (bin\\win_x64)",
        "km_injector_tip": "Such files come from ReShade, ASI loaders, DLSS/FSR replacements. To test, temporarily move the DLL out of the game folder (Steam «Verify integrity» restores only game files).",
        "exc_access": "ACCESS_VIOLATION — invalid memory access: most often an incompatible mod, overlay/hook, unstable overclock or driver.",
        "exc_noncont": "NONCONTINUABLE_EXCEPTION — fatal error after a previous one: often low memory, a mod or third-party module.",
        "exc_stack": "STACK_OVERFLOW — often a broken mod or save.",
        "exc_overrun": "STACK_BUFFER_OVERRUN — memory corruption guard: overlays/hooks or damaged game files.",
        "exc_illegal": "ILLEGAL_INSTRUCTION — the CPU could not execute an instruction: unstable CPU overclock or damaged files.",
        "exc_cpp": "C++ exception — internal game error; check game.log.txt before the crash.",
        "exc_break": "BREAKPOINT — the game stopped on an internal check (assert): often caused by a mod.",
        "exc_heap": "HEAP_CORRUPTION — memory corruption: mods, overlays or unstable RAM.",
        "exc_nomem": "NO_MEMORY — out of memory.",
        "exc_other": "Unknown crash type — search the code online.",
        "net_check_head": "🔌 Connection check now",
        "net_checking": "⏳ Checking connection...",
        "net_check_good": "All servers reachable — internet and DNS work.",
        "net_check_bad": "Some servers are unreachable — check internet, DNS, VPN, firewall/antivirus.",
        "net_no_log": "net.log not found.",
        "net_head": "🌐 net.log: game sessions {}, network errors {}",
        "net_hint": "Errors connecting the game to SCS online services (World of Trucks, news, convoy). They usually do not affect single-player.",
        "net_no_errors": "✅ No network errors.",
        "net_timeout": "⏱ Connection timeout (10060)",
        "net_timeout_tip": "The server did not respond in time: unstable internet, VPN or a firewall/ISP block.",
        "net_timeout_query": "connection timeout 10060",
        "net_refused": "⛔ Connection refused (10061)",
        "net_refused_tip": "The server refused the connection — it may be temporarily down.",
        "net_refused_query": "connection refused 10061",
        "net_reset": "🔌 Connection reset (10054)",
        "net_reset_tip": "The connection was dropped by the network or server: unstable Wi-Fi/VPN, antivirus traffic scanning.",
        "net_reset_query": "recv failed 10054",
        "net_unreach": "🚫 Network unreachable",
        "net_unreach_tip": "The PC had no network access at that moment.",
        "net_unreach_query": "network unreachable",
        "net_dns": "🔎 Server not found (DNS)",
        "net_dns_tip": "The server address could not be resolved: check the internet or change DNS (e.g. 1.1.1.1 or 8.8.8.8).",
        "net_dns_query": "online.scssoft.com getaddrinfo failed",
        "net_http": "🌐 Could not connect (HTTP)",
        "net_http_tip": "The game could not download data from the SCS server — usually a consequence of the errors above.",
        "net_http_query": "Unable to connect online.scssoft.com",
        "net_other": "❔ Other network errors",
        "net_other_tip": "See net.log for details.",
        "net_other_query": "net.log error",
        "wot_denied": "🔑 World of Trucks: access denied",
        "wot_denied_tip": "The World of Trucks session expired: in the game open your profile → World of Trucks and sign in again (or relink the account).",
        "wot_denied_query": "World of Trucks Access denied",
        "wot_notfound": "❓ World of Trucks: not found",
        "wot_notfound_tip": "The server did not find profile/contract data — often after switching profile or account; usually harmless.",
        "wot_notfound_query": "World of Trucks LINK ERRO Not found",
        "wot_network": "📡 World of Trucks: network error",
        "wot_network_tip": "World of Trucks communication failed — see connection resets above.",
        "wot_network_query": "World of Trucks network error",
        "wot_unregistered": "📝 World of Trucks: game not registered",
        "wot_unregistered_tip": "The game is not linked to a World of Trucks account — link it in the game profile.",
        "wot_unregistered_query": "World of Trucks Game is not registered",
        "wot_other": "🔗 World of Trucks: other error",
        "wot_other_tip": "See net.log for details.",
        "wot_other_query": "World of Trucks LINK ERRO",
        "report_title": "diagnostic report",
        "report_game": "Game",
        "report_mods": "Active mods in profile: {}",
        "report_missing": "Missing mods: {}",
        "report_outdated": "Mods without a version for the game: {}",
        "report_log": "game.log.txt: errors {}, warnings {}; top sources:",
        "report_crashes": "Crashes in game.crash.txt: {}; latest:",
        "report_suspects": "Suspect modules",
        "report_net": "Network errors in net.log: {}",
        "report_copied": "Report copied to the clipboard — paste it (Ctrl+V) on a forum or in Discord.",
        "btn_open_crash_file": "💥 game.crash.txt",
        "log_view_groups": "Groups",
        "log_view_errors": "Errors",
        "log_view_warnings": "Warnings",
        "log_missing": "game.log.txt has not been created by the game yet.",
        "log_clean": "🎉 Nothing found — the log is clean.",
        "log_game_version": "game version {}",
        "log_groups_head": "Problem sources: {}  (📦 — mod, ⚙ — game file or engine message)",
        "log_n_errors": "errors: {}",
        "log_n_warnings": "warnings: {}",
        "log_shown": "--- Showing {} of {} ---",
        "crash_none": "✅ No crash reports (game.crash.txt).",
        "crash_total": "💥 Crashes in game.crash.txt: {}  ·  {}",
        "crash_last": "Last: {}  ·  version {}  ·  {}  ·  module: {}",
        "crash_third_party": "⚠ Third-party modules in the call stack: {} (ReShade, overlays, plugins — try disabling them)",
        "crash_outdated": "ℹ The last crash happened in a different game version (now {}), so it is most likely outdated.",
        "clean_title": "Cache Cleanup",
        "clean_desc": "Clearing cache fixes crashes, texture bugs, and long loading times after mod updates.",
        "clean_current_btn": "🧹 Clear Cache for {}",
        "clean_both_btn": "🧹 Clear Cache for ETS 2 and ATS",
        "clean_cache_size": "Cache size: {}",
        "clean_confirm": "Delete cache folder?\n\n{}",
        "clean_ok": "Cache cleared successfully!",
        "bak_title": "Game Auto-Backups",
        "bak_desc": "On every update the game copies all profiles into steam_profiles(version).bak and never removes old copies. The app moves them to the Recycle Bin, so they can be restored.",
        "bak_summary": "Backups: {} · {}  —  to remove: {} · {}",
        "bak_none": "No game auto-backups found.",
        "bak_keep": "Keep newest:",
        "bak_clean_btn": "🗑 Move Old Ones to Recycle Bin",
        "bak_confirm": "Move {} old backups to the Recycle Bin? The {} newest will be kept.\n\n{}",
        "bak_done": "Old game backups moved to the Recycle Bin: {}",
        "bak_failed": "Could not move to the Recycle Bin (files may be in use):\n{}",
        "shots_title": "Screenshots",
        "shots_info": "Files: {}  ·  Size: {}\nFolder: {}",
        "shots_open": "📂 Open Screenshots",
        "mods_title": "Modifications Folder (mod)",
        "mods_info": "Path: {}\nTotal Size: {} (Mod files: {})",
        "btn_open_mods": "📂 Open mod Folder",
        "btn_mods_refresh": "🔄 Rescan",
        "btn_workshop_subs": "🌐 Workshop Subscriptions",
        "mods_folder_line": "mod folder: {}",
        "mods_workshop_line": "Steam Workshop: {}",
        "mods_scanning": "⏳ Scanning mods...",
        "mods_view_order": "Profile load order",
        "mods_view_problems": "Problems",
        "mods_view_unused": "Unused",
        "mods_search_placeholder": "Search mods by name...",
        "mods_summary": "Enabled in profile: {}  ·  installed: {} in mod + {} Workshop ({})\nMissing: {}  ·  No version for current game: {}  ·  Not used by any profile: {} ({})",
        "mods_order_head": "Enabled mods of this profile: {}  (top — highest priority, as in the in-game Mod Manager)",
        "mods_note_missing": "not on disk",
        "mods_note_outdated": "no version for {}",
        "mods_no_problems": "✅ No problems: all enabled mods are present and have a version for the current game.",
        "mods_missing_head": "❌ Enabled but missing on disk: {}",
        "mods_missing_hint": "The game skips these mods, and saves that depend on them may fail to load. Resubscribe in the Workshop or put the file back into the mod folder.",
        "mods_outdated_head": "⚠ No version for game {1}: {0}",
        "mods_outdated_hint": "The mod author has not updated it yet (versions.sii). Such mods can cause errors or crashes.",
        "mods_no_unused": "✅ Every installed mod is used by at least one profile.",
        "mods_unused_head": "💤 Not enabled in any profile of this game: {} ({})",
        "mods_unused_hint": "Unsubscribe via «Workshop Subscriptions» — Steam removes the files itself. All profiles of the game were checked.",
        "settings_title": "Game Directory Paths",
        "server_card_title": "Convoy Server (server_config.sii)",
        "server_btn": "⚙ Configure Server",
        "server_missing": "server_config.sii has not been created by the game yet:\n{}",
        "server_summary": "Name: {}  ·  max players: {}  ·  password: {}  ·  moderators: {}\n{}\nThis file is used by the Convoy Dedicated Server. If your server has its own folder, copy the file there.",
        "server_saved": "server_config.sii saved (a backup was created next to it).",
        "server_title": "Convoy Server Settings",
        "server_sec_general": "General",
        "server_sec_traffic": "Traffic",
        "server_sec_rules": "Session rules",
        "server_sec_network": "Network",
        "server_sec_moderators": "Moderators",
        "server_f_lobby_name": "Session name",
        "server_f_description": "Description",
        "server_f_welcome_message": "Welcome message",
        "server_f_password": "Password",
        "server_f_max_players": "Max players",
        "server_f_timezones": "Time zones",
        "server_f_traffic": "Traffic enabled",
        "server_f_max_vehicles_total": "Max vehicles total",
        "server_f_max_ai_vehicles_player": "Max AI vehicles per player",
        "server_f_max_ai_vehicles_player_spawn": "AI vehicles per player on spawn",
        "server_f_player_damage": "Players can damage each other",
        "server_f_hide_in_company": "Hide other players in company areas",
        "server_f_hide_colliding": "Hide colliding vehicles after teleport",
        "server_f_force_speed_limiter": "Force speed limiter",
        "server_f_mods_optioning": "Optional mods are truly optional",
        "server_f_service_no_collision": "No collisions in service areas",
        "server_f_in_menu_ghosting": "No collisions while a player is paused",
        "server_f_name_tags": "Player name tags",
        "server_f_friends_only": "Friends only (not used by dedicated server)",
        "server_f_show_server": "Show server (not used by dedicated server)",
        "server_f_connection_virtual_port": "Connection virtual port",
        "server_f_query_virtual_port": "Query virtual port",
        "server_f_connection_dedicated_port": "Connection dedicated port",
        "server_f_query_dedicated_port": "Query dedicated port",
        "server_f_server_logon_token": "Steam logon token",
        "server_f_moderator_list": "Moderator Steam IDs — one per line (promoted automatically on join):",
        "server_err_int": "«{}» must be a whole number.",
        "server_err_len": "«{}»: at most {} bytes (non-Latin letters take 2 bytes).",
        "server_err_range": "«{}»: allowed from {} to {}.",
        "server_err_ids": "Moderator Steam IDs must contain digits only.",
        "server_err_same_port": "«{}» must differ from the connection port.",
        "yes": "yes",
        "no": "no",
        "settings_desc": "The path is detected automatically (Steam -homedir launch option or Documents). Set a folder manually if your game data lives elsewhere:",
        "path_src_manual": "set manually",
        "path_src_homedir": "auto: -homedir from Steam launch options",
        "path_src_documents": "auto: Documents folder",
        "reset_paths_done": "Paths detected automatically:\n\n{}",
        "btn_reset_paths": "↺ Detect Paths Automatically",
        "btn_open_logs": "📋 Open App Logs Folder",
        "steam_sales_label": "Steam Sales:",
        "steam_checking": "Checking Steam prices...",
        "steam_none": "No Steam discounts right now",
        "dlg_error": "Error",
        "dlg_warning": "Warning",
        "dlg_confirm": "Confirm",
        "dlg_delete": "Delete",
        "dlg_restore": "Restore",
        "dlg_cleanup": "Cleanup",
        "dlg_info": "Information",
        "dlg_result": "Result",
        "dlg_cleanup_both": "Clean both games",
        "dlg_save_editor": "Save Editor",
        "dlg_pick_game_folder": "Select the data folder of {}",
        "msg_folder_missing": "Folder does not exist:\n{}",
        "msg_dir_not_found": "Folder not found:\n{}",
        "fps_unlimited": "Unlimited",
        "msg_launching": "Launching {} via Steam...",
        "preset_row_info": "Files: {}  •  Modified: {}",
        "btn_pick": "Select",
        "btn_picked": "Selected ✓",
        "msg_backup_created": "Backup created: {}",
        "msg_cfg_missing_short": "🔴 config.cfg not found",
        "msg_cfg_missing": "config.cfg not found:\n{}",
        "saves_empty": "No save archives yet. Press «📦 Create Save ZIP Backup» above.",
        "archive_row_info": "Size: {}  •  Created: {}  •  {}",
        "msg_saves_zipped": "Saves archived: {}",
        "msg_pick_archive": "Please select an archive from the list!",
        "msg_bad_slot_name": "Invalid slot name in the archive: {!r}",
        "msg_zip_corrupt": "Corrupted file in the archive: {}",
        "msg_delete_archive": "Do you really want to delete the archive {}?",
        "msg_log_read_error": "Error reading the log: {}",
        "crash_driver": " · driver ",
        "msg_cache_clean_already": "The cache folder was not found (already clean).",
        "msg_clean_both_confirm": "Delete the cache folders of ETS 2 and ATS?",
        "msg_cache_cleaned": "✓ {}: cache cleared",
        "msg_cache_clean_failed": "✗ {}: error ({})",
        "msg_cache_was_clean": "— {}: cache already clean",
        "msg_cache_done": "Cache cleanup finished",
        "msg_folder_not_game_data": "No config.cfg or profiles found in the selected folder.\n\nPath: {}\n\nHint: you need the game DATA folder (with profiles and config.cfg), NOT the Steam installation folder.\n\nUse this folder anyway?",
        "msg_path_updated": "Path updated: {}",
        "steam_sales_prefix": "🏷️ Steam Sales: ",
        "ready_status": "Ready  ·  {}  ·  profiles: {}",
        "ready": "Ready"
    }
}

class Tooltip:
    """Підказка при наведенні для кнопок-іконок."""
    def __init__(self, widget, text: str):
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.widget.after(450, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#2D3039", fg="#F9FAFB", relief="solid", borderwidth=1,
                 font=("Segoe UI", 9), padx=6, pady=3).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class RadioStationDialog(ctk.CTkToplevel):
    """Діалог додавання станції: URL, назва, жанр, мова, бітрейт."""
    def __init__(self, master, t):
        super().__init__(master)
        self.t = t
        self.result = None
        self.title(t('radio_add_title'))
        self.resizable(False, False)
        self.transient(master)
        self.grid_columnconfigure(1, weight=1)

        self.entries = {}
        fields = (("url", "radio_field_url", "https://..."), ("name", "radio_field_name", ""),
                  ("genre", "radio_field_genre", "Pop"), ("lang", "radio_field_lang", "UA"),
                  ("bitrate", "radio_field_bitrate", "128"))
        for row, (key, label, placeholder) in enumerate(fields):
            ctk.CTkLabel(self, text=t(label), font=ctk.CTkFont(family="Segoe UI", size=12)
                         ).grid(row=row, column=0, padx=(16, 8), pady=6, sticky="w")
            e = ctk.CTkEntry(self, width=360, placeholder_text=placeholder)
            e.grid(row=row, column=1, padx=(0, 16), pady=6, sticky="ew")
            self.entries[key] = e

        self.lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_DANGER)
        self.lbl_error.grid(row=len(fields), column=0, columnspan=2, padx=16, sticky="w")

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=len(fields) + 1, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="e")
        ctk.CTkButton(box, text=t('radio_check_btn'), width=110, fg_color="#374151", hover_color="#4B5563",
                      command=self._check).pack(side="left", padx=4)
        ctk.CTkButton(box, text="OK", width=90, fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER,
                      command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(box, text=t('cancel'), width=90, fg_color="#374151", hover_color="#4B5563",
                      command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.entries["url"].focus_set()
        except Exception:
            pass

    def _values(self) -> dict | None:
        vals = {k: e.get().strip() for k, e in self.entries.items()}
        if not re.match(r'^https?://\S+$', vals["url"]):
            self.lbl_error.configure(text=self.t('radio_bad_url'))
            return None
        if not vals["name"]:
            self.lbl_error.configure(text=self.t('radio_bad_name'))
            return None
        vals["lang"] = vals["lang"].upper()[:3]
        vals["bitrate"] = vals["bitrate"] if vals["bitrate"].isdigit() else "128"
        vals["favorite"] = "0"
        return vals

    def _check(self):
        vals = self._values()
        if not vals:
            return
        self.lbl_error.configure(text=self.t('radio_checking_one'), text_color=COLOR_TEXT_MUTED)
        result = {}
        threading.Thread(target=lambda: result.update(r=check_radio_stream(vals["url"])), daemon=True).start()

        def poll():
            if "r" not in result:
                self.after(200, poll)
                return
            ok, desc = result["r"]
            self.lbl_error.configure(text=("✓ " if ok else "✗ ") + desc, text_color="#34D399" if ok else COLOR_DANGER)
        self.after(200, poll)

    def _ok(self):
        vals = self._values()
        if vals:
            self.result = vals
            self.destroy()


class RadioSearchDialog(ctk.CTkToplevel):
    """Пошук станцій у каталозі Radio Browser і додавання вибраних або автоматично — найкращих робочих.
    on_add(станції) -> скільки додано; present() -> множина адрес, що вже є у списку гри."""
    def __init__(self, master, t, on_add, present, default_country: str = ""):
        from tkinter import ttk
        super().__init__(master)
        self.t, self.on_add, self.present = t, on_add, present
        self.results, self.status = [], {}
        self._busy = False
        self.title(t('rsearch_title'))
        self.geometry("980x600")
        self.minsize(760, 440)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=0, column=0, padx=14, pady=(14, 4), sticky="ew")
        form.grid_columnconfigure(0, weight=3)
        form.grid_columnconfigure(1, weight=2)
        self.e_name = ctk.CTkEntry(form, placeholder_text=t('rsearch_name_ph'), height=34)
        self.e_name.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.e_tag = ctk.CTkEntry(form, placeholder_text=t('rsearch_tag_ph'), height=34)
        self.e_tag.grid(row=0, column=1, padx=6, sticky="ew")
        self.countries = [t('rsearch_any_country')] + list(RADIO_COUNTRIES)
        self.cb_country = ctk.CTkOptionMenu(form, values=self.countries, width=150, height=34,
                                            fg_color="#374151", button_color="#4B5563", button_hover_color=UA_BLUE_HOVER)
        self.cb_country.set(default_country if default_country in RADIO_COUNTRIES else self.countries[0])
        self.cb_country.grid(row=0, column=2, padx=6)
        self.btn_search = ctk.CTkButton(form, text=t('rsearch_btn'), width=110, height=34,
                                        fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER, command=self.search)
        self.btn_search.grid(row=0, column=3, padx=(6, 0))

        self.lbl_info = ctk.CTkLabel(self, text=t('rsearch_hint'), text_color=COLOR_TEXT_MUTED, justify="left",
                                     font=ctk.CTkFont(family="Segoe UI", size=11), wraplength=940)
        self.lbl_info.grid(row=1, column=0, padx=16, pady=(2, 4), sticky="w")

        table = ctk.CTkFrame(self, corner_radius=10, fg_color="#18191D")
        table.grid(row=2, column=0, padx=14, pady=4, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        cols = ("name", "genre", "country", "codec", "bitrate", "votes", "status")
        widths = {"name": 260, "genre": 170, "country": 60, "codec": 60, "bitrate": 60, "votes": 70, "status": 150}
        self.tree = ttk.Treeview(table, columns=cols, show="headings", style="Radio.Treeview", selectmode="extended")
        for c in cols:
            self.tree.heading(c, text=t(f'rsearch_col_{c}'))
            self.tree.column(c, width=widths[c], stretch=c in ("name", "genre"),
                             anchor="w" if c in ("name", "genre", "status") else "center")
        self.tree.tag_configure("have", foreground=COLOR_TEXT_MUTED)
        self.tree.tag_configure("dead", foreground="#F87171")
        self.tree.tag_configure("alive", foreground="#34D399")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb = ctk.CTkScrollbar(table, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.add_selected())

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, padx=14, pady=(6, 14), sticky="ew")
        self.btn_check = ctk.CTkButton(bar, text=t('btn_radio_check'), height=32, fg_color="#374151",
                                       hover_color="#4B5563", command=self.check_selected)
        self.btn_check.pack(side="left", padx=4)
        self.btn_add = ctk.CTkButton(bar, text=t('rsearch_add_selected'), height=32, fg_color=UA_BLUE,
                                     hover_color=UA_BLUE_HOVER, command=self.add_selected)
        self.btn_add.pack(side="left", padx=4)
        ctk.CTkLabel(bar, text=t('rsearch_auto_label')).pack(side="left", padx=(16, 4))
        self.cb_auto_n = ctk.CTkOptionMenu(bar, values=["5", "10", "20"], width=70, height=32,
                                           fg_color="#374151", button_color="#4B5563", button_hover_color=UA_BLUE_HOVER)
        self.cb_auto_n.set("10")
        self.cb_auto_n.pack(side="left", padx=4)
        self.btn_auto = ctk.CTkButton(bar, text=t('rsearch_auto_btn'), height=32, fg_color=UA_YELLOW,
                                      hover_color=UA_YELLOW_HOVER, text_color=UA_YELLOW_TEXT_DARK, command=self.auto_add)
        self.btn_auto.pack(side="left", padx=4)
        Tooltip(self.btn_auto, t('rsearch_auto_tip'))
        ctk.CTkButton(bar, text=t('close'), width=90, height=32, fg_color="#374151", hover_color="#4B5563",
                      command=self.destroy).pack(side="right", padx=4)

        for e in (self.e_name, self.e_tag):
            e.bind("<Return>", lambda _e: self.search())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.e_name.focus_set()
        except Exception:
            pass

    def _set_busy(self, busy: bool):
        self._busy = busy
        for b in (self.btn_search, self.btn_check, self.btn_add, self.btn_auto):
            b.configure(state="disabled" if busy else "normal")

    def _run(self, work, done):
        """work() у фоновому потоці; done(результат або виняток) — у головному."""
        box = {}

        def runner():
            try:
                box['r'] = work()
            except Exception as e:
                box['r'] = e
        self._set_busy(True)
        threading.Thread(target=runner, daemon=True).start()

        def poll():
            if not self.winfo_exists():
                return
            if 'r' not in box:
                self.after(150, poll)
                return
            self._set_busy(False)
            done(box['r'])
        self.after(150, poll)

    def search(self):
        if self._busy:
            return
        name, tag = self.e_name.get().strip(), self.e_tag.get().strip()
        country = self.cb_country.get()
        country = country if country in RADIO_COUNTRIES else ""
        self.lbl_info.configure(text=self.t('rsearch_searching'), text_color=COLOR_TEXT_MUTED)

        def done(res):
            if isinstance(res, Exception):
                self.lbl_info.configure(text=self.t('rsearch_error').format(res), text_color=COLOR_DANGER)
                return
            self.results = res
            self.render()
            text = self.t('rsearch_found').format(len(res)) if res else self.t('rsearch_nothing')
            self.lbl_info.configure(text=text, text_color=COLOR_TEXT_WHITE if res else "#FBBF24")
        self._run(lambda: search_radio_browser(name, tag, country), done)

    def render(self):
        present = self.present()
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for i, st in enumerate(self.results):
            ok, desc = self.status.get(st['url'], (None, ""))
            if st['url'] in present:
                tags, status = ("have",), self.t('rsearch_in_game')
            else:
                tags = ("alive",) if ok is True else ("dead",) if ok is False else ()
                status = (("✓ " if ok else "✗ ") + desc) if ok is not None else ""
            self.tree.insert("", "end", iid=str(i), tags=tags, values=(
                st['name'], st['genre'], st['lang'], st['codec'], st['bitrate'], st['votes'], status))
        for iid in sel:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

    def _selected(self) -> list:
        return [self.results[int(i)] for i in self.tree.selection()]

    def check_selected(self):
        sel = self._selected()
        if not sel:
            self.lbl_info.configure(text=self.t('radio_pick'), text_color="#FBBF24")
            return
        urls = [s['url'] for s in sel]
        self.lbl_info.configure(text=self.t('radio_checking').format(0, len(urls)), text_color=COLOR_TEXT_MUTED)

        def work():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=16) as pool:
                return dict(zip(urls, pool.map(check_radio_stream, urls)))

        def done(res):
            if isinstance(res, Exception):
                return
            self.status.update(res)
            self.render()
            dead = sum(1 for ok, _ in res.values() if not ok)
            self.lbl_info.configure(text=self.t('radio_check_done').format(len(res) - dead, dead),
                                    text_color=COLOR_TEXT_WHITE)
        self._run(work, done)

    def add_selected(self):
        sel = [s for s in self._selected() if s['url'] not in self.present()]
        if not sel:
            self.lbl_info.configure(text=self.t('radio_pick'), text_color="#FBBF24")
            return
        added = self.on_add(sel)
        self.render()
        self.lbl_info.configure(text=self.t('rsearch_added').format(added), text_color="#34D399")

    def auto_add(self):
        """Перевіряє найпопулярніші нові станції з результатів і додає перші N, що реально грають."""
        if not self.results:
            self.lbl_info.configure(text=self.t('rsearch_need_results'), text_color="#FBBF24")
            return
        want = int(self.cb_auto_n.get())
        present = self.present()
        candidates = [s for s in sorted(self.results, key=lambda s: -s['votes'])
                      if s['url'] not in present and self.status.get(s['url'], (None,))[0] is not False]
        if not candidates:
            self.lbl_info.configure(text=self.t('rsearch_all_in_game'), text_color="#FBBF24")
            return
        self.lbl_info.configure(text=self.t('rsearch_auto_running').format(want), text_color=COLOR_TEXT_MUTED)
        known = dict(self.status)

        def work():
            from concurrent.futures import ThreadPoolExecutor
            good, checked = [], {}
            pos = 0
            with ThreadPoolExecutor(max_workers=12) as pool:
                while len(good) < want and pos < len(candidates):
                    batch = candidates[pos:pos + max(4, (want - len(good)) * 2)]
                    pos += len(batch)
                    todo = [s['url'] for s in batch if s['url'] not in known]
                    checked.update(zip(todo, pool.map(check_radio_stream, todo)))
                    for s in batch:
                        res = known.get(s['url']) or checked.get(s['url'])
                        if res and res[0] and len(good) < want:
                            good.append(s)
            return good, checked

        def done(res):
            if isinstance(res, Exception):
                self.lbl_info.configure(text=self.t('rsearch_error').format(res), text_color=COLOR_DANGER)
                return
            good, checked = res
            self.status.update(checked)
            added = self.on_add(good) if good else 0
            self.render()
            dead = sum(1 for ok, _ in checked.values() if not ok)
            self.lbl_info.configure(text=self.t('rsearch_auto_done').format(added, len(checked), dead),
                                    text_color="#34D399" if added else "#FBBF24")
        self._run(work, done)


class FaqDialog(ctk.CTkToplevel):
    """Часті питання: пошук, відповідь розгортається кліком, кнопка переходу на потрібну вкладку."""
    def __init__(self, master, t, open_tab):
        super().__init__(master)
        self.t, self.open_tab = t, open_tab
        self.title(t('faq_title'))
        self.geometry("880x660")
        self.minsize(640, 420)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self, text=f"❓ {t('faq_title')}", text_color=UA_YELLOW,
                     font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold")
                     ).grid(row=0, column=0, padx=18, pady=(14, 4), sticky="w")
        self.e_search = ctk.CTkEntry(self, placeholder_text=t('faq_search_ph'), height=34)
        self.e_search.grid(row=1, column=0, padx=16, pady=(2, 6), sticky="ew")
        self.e_search.bind("<KeyRelease>", lambda e: self.render())

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, padx=10, pady=4, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self.lbl_empty = ctk.CTkLabel(self.scroll, text=t('faq_nothing'), text_color=COLOR_TEXT_MUTED)

        self.items = []
        for n, tab_key in FAQ_ITEMS:
            q, a = t(f'faq_q{n}'), t(f'faq_a{n}')
            card = ctk.CTkFrame(self.scroll, corner_radius=8, fg_color=COLOR_CARD_INNER)
            card.grid_columnconfigure(0, weight=1)
            idx = len(self.items)
            btn = ctk.CTkButton(card, text=f"▸  {q}", anchor="w", height=36, fg_color="transparent",
                                hover_color=COLOR_CARD_HOVER, text_color=COLOR_TEXT_WHITE,
                                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                                command=lambda i=idx: self.toggle(i))
            btn.grid(row=0, column=0, padx=4, pady=4, sticky="ew")
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.grid_columnconfigure(0, weight=1)
            answer = ctk.CTkLabel(body, text=a, justify="left", anchor="w", wraplength=780, text_color="#D1D5DB",
                                  font=ctk.CTkFont(family="Segoe UI", size=12))
            answer.grid(row=0, column=0, padx=(26, 14), pady=(0, 8), sticky="w")
            if tab_key:
                ctk.CTkButton(body, text=t('faq_go_tab').format(t(tab_key)), height=28,
                              fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER,
                              command=lambda k=tab_key: self._go(k)
                              ).grid(row=1, column=0, padx=(26, 14), pady=(0, 10), sticky="w")
            self.items.append({'card': card, 'btn': btn, 'body': body, 'answer': answer, 'q': q,
                               'text': f"{q} {a}".lower(), 'open': False})

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, padx=16, pady=(6, 14), sticky="ew")
        ctk.CTkLabel(bar, text=t('faq_footer'), text_color=COLOR_TEXT_MUTED,
                     font=ctk.CTkFont(family="Segoe UI", size=11)).pack(side="left")
        ctk.CTkButton(bar, text=t('close'), width=100, height=32, fg_color="#374151", hover_color="#4B5563",
                      command=self.destroy).pack(side="right")

        self.render()
        self._wrap = 0
        self.bind("<Configure>", self._on_resize)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.e_search.focus_set()
        except Exception:
            pass

    def _on_resize(self, event):
        if event.widget is not self:
            return
        wrap = max(400, int((self.winfo_width() / self._get_window_scaling()) - 130))
        if abs(wrap - self._wrap) > 20:
            self._wrap = wrap
            for it in self.items:
                it['answer'].configure(wraplength=wrap)

    def _set_open(self, i: int, is_open: bool):
        it = self.items[i]
        it['open'] = is_open
        if is_open:
            it['body'].grid(row=1, column=0, sticky="ew")
        else:
            it['body'].grid_remove()
        it['btn'].configure(text=("▾  " if is_open else "▸  ") + it['q'])

    def toggle(self, i: int):
        self._set_open(i, not self.items[i]['open'])

    def render(self):
        words = self.e_search.get().strip().lower().split()
        row = 0
        for i, it in enumerate(self.items):
            if all(w in it['text'] for w in words):
                it['card'].grid(row=row, column=0, padx=4, pady=3, sticky="ew")
                row += 1
                if words:
                    self._set_open(i, True)
            else:
                it['card'].grid_remove()
        if row:
            self.lbl_empty.grid_remove()
        else:
            self.lbl_empty.grid(row=0, column=0, pady=20)

    def visible_questions(self) -> list:
        return [it['q'] for it in self.items if it['card'].winfo_manager()]

    def _go(self, tab_key: str):
        self.open_tab(tab_key)
        self.destroy()


class ModSortDialog(ctk.CTkToplevel):
    """Попередній перегляд автосортування модів; result=True — застосувати."""
    def __init__(self, master, t, summary: str, lines: list):
        super().__init__(master)
        self.t = t
        self.result = False
        self.title(t('modsort_title'))
        self.geometry("920x640")
        self.minsize(700, 420)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text=summary, justify="left", wraplength=880,
                     font=ctk.CTkFont(family="Segoe UI", size=12)).grid(row=0, column=0, padx=16, pady=(14, 6), sticky="w")
        box = ctk.CTkTextbox(self, corner_radius=10, fg_color="#18191D", wrap="none",
                             font=ctk.CTkFont(family="Consolas", size=12), text_color="#D1D5DB")
        box.grid(row=1, column=0, padx=14, pady=4, sticky="nsew")
        box.tag_config("head", foreground=UA_YELLOW)
        box.tag_config("up", foreground="#34D399")
        box.tag_config("down", foreground="#FBBF24")
        box.tag_config("muted", foreground=COLOR_TEXT_MUTED)
        for text, tag in lines:
            box.insert("end", text, tag)
        box.configure(state="disabled")
        self.box = box

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, padx=14, pady=(6, 14), sticky="e")
        ctk.CTkButton(bar, text=t('modsort_apply'), height=34, fg_color=UA_YELLOW, hover_color=UA_YELLOW_HOVER,
                      text_color=UA_YELLOW_TEXT_DARK, command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(bar, text=t('cancel'), width=100, height=34, fg_color="#374151", hover_color="#4B5563",
                      command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.focus_set()
        except Exception:
            pass

    def _ok(self):
        self.result = True
        self.destroy()


class SaveEditorDialog(ctk.CTkToplevel):
    """Редактор слота: гроші, досвід і навички (ADR — 6 класів бітовою маскою, решта 0..6)."""
    ADR_CLASSES = ("1", "2", "3", "4", "6", "8")
    SKILLS = ("long_dist", "heavy", "fragile", "urgent", "mechanical")

    def __init__(self, master, slot_title: str, values: dict, currency: str, t):
        super().__init__(master)
        self.t = t
        self.result = None
        self.title(t('editor_title').format(slot_title))
        self.resizable(False, False)
        self.transient(master)
        self.grid_columnconfigure(1, weight=1)
        font = ctk.CTkFont(family="Segoe UI", size=12)
        bold = ctk.CTkFont(family="Segoe UI", size=12, weight="bold")

        ctk.CTkLabel(self, text=t('editor_money').format(currency), font=bold).grid(row=0, column=0, padx=(16, 8), pady=(16, 6), sticky="w")
        self.e_money = ctk.CTkEntry(self, width=200)
        self.e_money.insert(0, str(values["money_account"]))
        self.e_money.grid(row=0, column=1, padx=(0, 16), pady=(16, 6), sticky="w")

        ctk.CTkLabel(self, text=t('editor_xp'), font=bold).grid(row=1, column=0, padx=(16, 8), pady=6, sticky="w")
        self.e_xp = ctk.CTkEntry(self, width=200)
        self.e_xp.insert(0, str(values["experience_points"]))
        self.e_xp.grid(row=1, column=1, padx=(0, 16), pady=6, sticky="w")

        ctk.CTkLabel(self, text=t('editor_adr'), font=bold).grid(row=2, column=0, padx=(16, 8), pady=6, sticky="nw")
        adr_box = ctk.CTkFrame(self, fg_color="transparent")
        adr_box.grid(row=2, column=1, padx=(0, 16), pady=6, sticky="w")
        self.adr_vars = []
        for i, cls in enumerate(self.ADR_CLASSES):
            var = ctk.BooleanVar(value=bool(values["adr"] >> i & 1))
            ctk.CTkCheckBox(adr_box, text=t('editor_adr_class').format(cls), variable=var, font=font, width=70,
                            fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER).grid(row=i // 3, column=i % 3, padx=(0, 10), pady=2, sticky="w")
            self.adr_vars.append(var)

        self.skill_menus = {}
        for row, key in enumerate(self.SKILLS, start=3):
            ctk.CTkLabel(self, text=t(f'editor_skill_{key}'), font=bold).grid(row=row, column=0, padx=(16, 8), pady=4, sticky="w")
            m = ctk.CTkOptionMenu(self, values=[str(i) for i in range(7)], width=80,
                                  fg_color="#374151", button_color="#4B5563", button_hover_color=UA_BLUE_HOVER)
            m.set(str(min(max(values[key], 0), 6)))
            m.grid(row=row, column=1, padx=(0, 16), pady=4, sticky="w")
            self.skill_menus[key] = m

        row = 3 + len(self.SKILLS)
        ctk.CTkLabel(self, text=t('editor_note'), font=ctk.CTkFont(family="Segoe UI", size=11),
                     text_color=COLOR_TEXT_MUTED, justify="left", wraplength=420
                     ).grid(row=row, column=0, columnspan=2, padx=16, pady=(10, 0), sticky="w")
        self.lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_DANGER)
        self.lbl_error.grid(row=row + 1, column=0, columnspan=2, padx=16, sticky="w")

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=row + 2, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="e")
        ctk.CTkButton(box, text=t('editor_save'), width=150, fg_color=UA_YELLOW, hover_color=UA_YELLOW_HOVER,
                      text_color=UA_YELLOW_TEXT_DARK, command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(box, text=t('cancel'), width=90, fg_color="#374151", hover_color="#4B5563",
                      command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.e_money.focus_set()
        except Exception:
            pass

    def _ok(self):
        try:
            money = int(self.e_money.get().replace(" ", "").replace(" ", ""))
            xp = int(self.e_xp.get().replace(" ", "").replace(" ", ""))
        except ValueError:
            self.lbl_error.configure(text=self.t('editor_bad_number'))
            return
        result = {"money_account": money, "experience_points": xp,
                  "adr": sum(1 << i for i, v in enumerate(self.adr_vars) if v.get())}
        result.update({k: int(m.get()) for k, m in self.skill_menus.items()})
        for field, value in result.items():
            _unit, _t, lo, hi = SAVE_EDIT_FIELDS[field]
            if not lo <= value <= hi:
                self.lbl_error.configure(text=self.t('editor_out_of_range').format(lo, hi))
                return
        self.result = result
        self.destroy()


class ServerConfigDialog(ctk.CTkToplevel):
    """Форма для server_config.sii: усі поля з SERVER_FIELDS, згруповані за розділами."""
    SECTIONS = (
        ("server_sec_general", ("lobby_name", "description", "welcome_message", "password", "max_players", "timezones")),
        ("server_sec_traffic", ("traffic", "max_vehicles_total", "max_ai_vehicles_player", "max_ai_vehicles_player_spawn")),
        ("server_sec_rules", ("player_damage", "hide_in_company", "hide_colliding", "force_speed_limiter",
                              "mods_optioning", "service_no_collision", "in_menu_ghosting", "name_tags",
                              "friends_only", "show_server")),
        ("server_sec_network", ("connection_virtual_port", "query_virtual_port", "connection_dedicated_port",
                                "query_dedicated_port", "server_logon_token")),
        ("server_sec_moderators", ("moderator_list",)),
    )

    def __init__(self, master, values: dict, t):
        super().__init__(master)
        self.t = t
        self.values = dict(values)
        self.result = None
        self.kinds = {k: (kind, p) for k, kind, p in SERVER_FIELDS}
        self.title(t('server_title'))
        self.geometry("640x640")
        self.minsize(560, 420)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        body.grid_columnconfigure(1, weight=1)
        bold = ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        small = ctk.CTkFont(family="Segoe UI", size=11)

        self.widgets = {}
        row = 0
        for sec_key, keys in self.SECTIONS:
            ctk.CTkLabel(body, text=t(sec_key), font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                         text_color=UA_YELLOW).grid(row=row, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w")
            row += 1
            for key in keys:
                if key not in values and key != "moderator_list":
                    continue
                kind, param = self.kinds[key]
                label = t(f'server_f_{key}')
                if kind == "bool":
                    var = ctk.BooleanVar(value=bool(values.get(key)))
                    ctk.CTkSwitch(body, text=label, variable=var, font=small, progress_color=UA_BLUE,
                                  button_color=UA_YELLOW, button_hover_color=UA_YELLOW_LIGHT
                                  ).grid(row=row, column=0, columnspan=2, padx=12, pady=3, sticky="w")
                    self.widgets[key] = var
                elif kind == "ids":
                    ctk.CTkLabel(body, text=label, font=small, text_color=COLOR_TEXT_MUTED, justify="left",
                                 wraplength=540).grid(row=row, column=0, columnspan=2, padx=12, sticky="w")
                    row += 1
                    box = ctk.CTkTextbox(body, height=90, font=ctk.CTkFont(family="Consolas", size=12))
                    box.insert("1.0", "\n".join(values.get(key, [])))
                    box.grid(row=row, column=0, columnspan=2, padx=12, pady=4, sticky="ew")
                    self.widgets[key] = box
                else:
                    hint = f" ({param[0]}–{param[1]})" if kind == "int" else ""
                    ctk.CTkLabel(body, text=label + hint, font=bold).grid(row=row, column=0, padx=12, pady=3, sticky="w")
                    e = ctk.CTkEntry(body, width=260, show="•" if key in ("password", "server_logon_token") else "")
                    e.insert(0, str(values.get(key, "")))
                    e.grid(row=row, column=1, padx=(0, 12), pady=3, sticky="ew")
                    self.widgets[key] = e
                row += 1

        self.lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_DANGER, justify="left", wraplength=600)
        self.lbl_error.grid(row=1, column=0, padx=16, pady=(4, 0), sticky="w")
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="e")
        ctk.CTkButton(box, text=t('editor_save'), width=150, fg_color=UA_YELLOW, hover_color=UA_YELLOW_HOVER,
                      text_color=UA_YELLOW_TEXT_DARK, command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(box, text=t('cancel'), width=90, fg_color="#374151", hover_color="#4B5563",
                      command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def collect(self) -> dict | None:
        out = dict(self.values)
        for key, w in self.widgets.items():
            kind, _ = self.kinds[key]
            if kind == "bool":
                out[key] = bool(w.get())
            elif kind == "ids":
                out[key] = [x.strip() for x in re.split(r'[\s,;]+', w.get("1.0", "end")) if x.strip()]
            elif kind == "int":
                try:
                    out[key] = int(w.get().strip())
                except ValueError:
                    self.lbl_error.configure(text=self.t('server_err_int').format(self.t(f'server_f_{key}')))
                    return None
            else:
                out[key] = w.get()
        errors = validate_server_config(out)
        if errors:
            msgs = []
            for key, kind, param in errors:
                name = self.t(f'server_f_{key}')
                if kind == "len":
                    msgs.append(self.t('server_err_len').format(name, param))
                elif kind == "range":
                    msgs.append(self.t('server_err_range').format(name, param[0], param[1]))
                elif kind == "ids":
                    msgs.append(self.t('server_err_ids'))
                else:
                    msgs.append(self.t('server_err_same_port').format(name))
            self.lbl_error.configure(text="\n".join(msgs))
            return None
        return out

    def _ok(self):
        res = self.collect()
        if res is not None:
            self.result = res
            self.destroy()


class SettingsManager:
    def __init__(self):
        self.file_path = get_app_dir() / CONFIG_FILE
        self._auto_cache = {}
        self.data = self._load()

    def _default(self):
        # Порожній шлях = визначати автоматично (див. get_auto_game_path)
        return {
            "language": "uk",
            "theme": "dark",
            "last_game": "Euro Truck Simulator 2",
            "game_paths": {game: "" for game in GAMES}
        }

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    default = self._default()
                    for k, v in default.items():
                        if k not in saved:
                            saved[k] = v
                    for game in GAMES:
                        if game not in saved["game_paths"]:
                            saved["game_paths"][game] = ""
                        # Старі версії записували стандартну папку «Документи» як явний шлях —
                        # вважаємо її автоматичною, щоб спрацювало визначення -homedir
                        elif saved["game_paths"][game] in self._legacy_defaults(game):
                            saved["game_paths"][game] = ""
                    return saved
            except Exception:
                pass
        return self._default()

    def save(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")

    @staticmethod
    def _legacy_defaults(game_name: str) -> set:
        name = GAMES.get(game_name, game_name)
        return {str(Path.home() / 'Documents' / name), str(get_documents_dir() / name)}

    def get_auto_game_path(self, game_name: str) -> tuple:
        """Автовизначення: -homedir з параметрів запуску Steam, інакше «Документи».
        Повертає (шлях, джерело) — джерело: 'homedir' або 'documents'."""
        cache = self._auto_cache
        if game_name not in cache:
            homedir = None
            try:
                homedir = find_homedir_from_steam(game_name)
            except Exception:
                pass
            if homedir:
                cache[game_name] = (homedir, 'homedir')
            else:
                cache[game_name] = (get_documents_dir() / GAMES.get(game_name, game_name), 'documents')
        return cache[game_name]

    def get_path_source(self, game_name: str) -> str:
        """'manual', 'homedir' або 'documents'."""
        if self.data.get("game_paths", {}).get(game_name, "").strip():
            return 'manual'
        return self.get_auto_game_path(game_name)[1]

    def get_game_path(self, game_name: str) -> Path:
        custom = self.data.get("game_paths", {}).get(game_name, "").strip()
        if custom:
            return Path(custom)
        return self.get_auto_game_path(game_name)[0]

    def set_game_path(self, game_name: str, path_str: str):
        if "game_paths" not in self.data:
            self.data["game_paths"] = {}
        self.data["game_paths"][game_name] = path_str.strip()
        self.save()

    def get_language(self) -> str:
        return self.data.get("language", "uk")

    def set_language(self, lang: str):
        self.data["language"] = lang
        self.save()

    def get_last_game(self) -> str:
        return self.data.get("last_game", "Euro Truck Simulator 2")

    def set_last_game(self, game: str):
        self.data["last_game"] = game
        self.save()


class TruckControlsApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.logger = setup_logger()
        self.settings = SettingsManager()
        self.lang = self.settings.get_language()
        self.current_game = self.settings.get_last_game()

        self.profiles = []
        self.selected_profile_index = 0
        self.presets = []
        self.selected_preset = None
        self.save_archives = []
        self.selected_save_archive = None

        self.title(f"{self.t('title')} v{APP_VERSION}")
        self.geometry("1060x820")
        self.minsize(960, 700)

        ico_path = get_resource_path("assets/app.ico")
        if ico_path.exists():
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass

        self._build_ui()
        self.refresh_all()
        self._show_ready_status()

        # Фоновий моніторинг запущеної гри
        self.after(1000, self.check_game_process_loop)
        # Перевірка знижок Steam
        self.after(500, self.fetch_steam_sales)
        self.after(1000, self._flush_tk_garbage_loop)

    def open_faq(self):
        dlg = FaqDialog(self, self.t, lambda key: self.tabview.set(self.t(key)))
        self.wait_window(dlg)

    def _flush_tk_garbage_loop(self):
        flush_deferred_tk_deletes()
        self.after(1000, self._flush_tk_garbage_loop)

    def t(self, key: str) -> str:
        return LANGUAGES.get(self.lang, LANGUAGES["uk"]).get(key, key)

    def get_active_game_path(self) -> Path:
        return self.settings.get_game_path(self.current_game)

    # Папки створюються лише під час запису (create=True), щоб не падати
    # на старті, якщо диск зі шляху відсутній, і не смітити в чужих папках.
    def get_presets_path(self, create: bool = False) -> Path:
        p = self.get_active_game_path() / PRESETS_DIR_NAME
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def get_saves_backup_path(self, create: bool = False) -> Path:
        p = self.get_active_game_path() / SAVES_BACKUP_DIR_NAME
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def _open_app_folder(self, getter):
        if not self.get_active_game_path().exists():
            messagebox.showwarning("!", self.t('msg_folder_missing').format(self.get_active_game_path()))
            return
        try:
            os.startfile(getter(create=True))
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. Шапка (Header)
        self.header_frame = ctk.CTkFrame(self, corner_radius=12, fg_color=COLOR_CARD_DARK)
        self.header_frame.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
        self.header_frame.grid_columnconfigure(1, weight=1)

        # Витончена декоративна смужка у кольорах прапора України
        ua_flag_bar = ctk.CTkFrame(self.header_frame, height=4, fg_color="transparent")
        ua_flag_bar.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(6, 2))
        ua_flag_bar.grid_columnconfigure(0, weight=1)
        stripe_top = ctk.CTkFrame(ua_flag_bar, height=2, fg_color=UA_BLUE, corner_radius=1)
        stripe_top.pack(fill="x")
        stripe_bot = ctk.CTkFrame(ua_flag_bar, height=2, fg_color=UA_YELLOW, corner_radius=1)
        stripe_bot.pack(fill="x")

        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.grid(row=1, column=0, padx=16, pady=(4, 8), sticky="w")

        logo_path = get_resource_path("assets/logo.png")
        self.header_logo_img = None
        if logo_path.exists():
            try:
                pil_logo = Image.open(logo_path)
                self.header_logo_img = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(46, 46))
            except Exception:
                pass

        if self.header_logo_img:
            self.lbl_logo = ctk.CTkLabel(title_box, image=self.header_logo_img, text="")
            self.lbl_logo.pack(side="left", padx=(0, 10))

        text_titles = ctk.CTkFrame(title_box, fg_color="transparent")
        text_titles.pack(side="left", anchor="w")

        self.lbl_main_title = ctk.CTkLabel(
            text_titles,
            text=f"🚛 {self.t('title')}",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_main_title.pack(anchor="w")

        self.lbl_sub_title = ctk.CTkLabel(
            text_titles,
            text=self.t('subtitle'),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_sub_title.pack(anchor="w")

        top_controls = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        top_controls.grid(row=1, column=2, padx=16, pady=(4, 8), sticky="e")

        # Перемикач мови: видно обидві мови, поточна підсвічена
        lang_box = ctk.CTkFrame(top_controls, fg_color="transparent")
        lang_box.pack(side="right", padx=4)
        self.lbl_lang = ctk.CTkLabel(lang_box, text="🌐", font=ctk.CTkFont(family="Segoe UI", size=14))
        self.lbl_lang.pack(side="left", padx=(0, 6))
        self.seg_lang = ctk.CTkSegmentedButton(
            lang_box,
            values=["UA", "EN"],
            command=self.on_language_selected,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            selected_color=UA_BLUE,
            selected_hover_color=UA_BLUE_HOVER,
            unselected_color="#272A30",
            height=32,
            width=96
        )
        self.seg_lang.set("UA" if self.lang == "uk" else "EN")
        self.seg_lang.pack(side="left")
        Tooltip(self.lbl_lang, "Мова інтерфейсу / Interface language")

        self.btn_header_refresh = ctk.CTkButton(
            top_controls,
            text=self.t('btn_refresh'),
            width=90,
            height=32,
            fg_color="#272A30",
            hover_color=UA_BLUE_HOVER,
            command=self.refresh_all
        )
        self.btn_header_refresh.pack(side="right", padx=4)

        self.btn_faq = ctk.CTkButton(
            top_controls,
            text=self.t('btn_faq'),
            width=80,
            height=32,
            fg_color="#272A30",
            hover_color=UA_BLUE_HOVER,
            command=self.open_faq
        )
        self.btn_faq.pack(side="right", padx=4)

        # 2. Панель гри: вибір гри, статус процесу, запуск; профіль і папка гри
        self.path_card = ctk.CTkFrame(self, corner_radius=12, fg_color=COLOR_CARD_DARK)
        self.path_card.grid(row=1, column=0, padx=16, pady=6, sticky="ew")
        self.path_card.grid_columnconfigure(0, weight=1)

        # Рядок вибору гри + статус процесу + кнопка запуску
        top_row = ctk.CTkFrame(self.path_card, fg_color="transparent")
        top_row.grid(row=0, column=0, padx=16, pady=(10, 4), sticky="ew")
        top_row.grid_columnconfigure(2, weight=1)

        self.seg_game = ctk.CTkSegmentedButton(
            top_row,
            values=list(GAMES.keys()),
            command=self.on_game_switched,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            selected_color=UA_BLUE,
            selected_hover_color=UA_BLUE_HOVER,
            height=34
        )
        self.seg_game.set(self.current_game)
        self.seg_game.grid(row=0, column=0, sticky="w")

        # Індикатор запущеної гри
        self.lbl_game_proc_status = ctk.CTkLabel(
            top_row,
            text=self.t('game_proc_closed'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLOR_SUCCESS
        )
        self.lbl_game_proc_status.grid(row=0, column=1, padx=16, sticky="w")

        # Кнопка запуску гри через Steam
        self.btn_launch_game = ctk.CTkButton(
            top_row,
            text=self.t('btn_launch_game'),
            height=32,
            width=140,
            fg_color=UA_BLUE,
            hover_color=UA_BLUE_HOVER,
            command=self.launch_game_via_steam
        )
        self.btn_launch_game.grid(row=0, column=3, sticky="e")

        # Рядок профілю (спільний для всіх вкладок) та папки гри
        ctx_row = ctk.CTkFrame(self.path_card, fg_color="transparent")
        ctx_row.grid(row=1, column=0, padx=16, pady=(4, 10), sticky="ew")
        ctx_row.grid_columnconfigure(1, weight=3)
        ctx_row.grid_columnconfigure(3, weight=2)

        self.lbl_prof_title = ctk.CTkLabel(
            ctx_row,
            text=self.t('profile_label'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.lbl_prof_title.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.cb_profile = ctk.CTkOptionMenu(
            ctx_row,
            values=[self.t('profile_select_prompt')],
            command=self.on_profile_selected,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color=UA_BLUE,
            button_color=UA_BLUE_HOVER,
            button_hover_color=UA_BLUE,
            height=32,
            dynamic_resizing=False
        )
        self.cb_profile.grid(row=0, column=1, padx=(0, 16), sticky="ew")

        self.lbl_path_desc = ctk.CTkLabel(
            ctx_row,
            text=self.t('current_game_path'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_path_desc.grid(row=0, column=2, padx=(0, 8), sticky="w")

        self.entry_path = ctk.CTkEntry(
            ctx_row,
            height=32,
            font=ctk.CTkFont(family="Consolas", size=11),
            state="readonly"
        )
        self.entry_path.grid(row=0, column=3, sticky="ew", padx=(0, 6))

        self.btn_browse = ctk.CTkButton(
            ctx_row,
            text="📂",
            height=32,
            width=40,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.browse_game_folder
        )
        self.btn_browse.grid(row=0, column=4, padx=(0, 6))
        Tooltip(self.btn_browse, self.t('btn_change_path'))

        self.btn_open_folder = ctk.CTkButton(
            ctx_row,
            text="↗",
            height=32,
            width=40,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.open_current_game_folder
        )
        self.btn_open_folder.grid(row=0, column=5)
        Tooltip(self.btn_open_folder, self.t('btn_open_game_folder'))

        # 3. Вкладки (Tabview). Порядок і ключі перекладу — у TAB_KEYS
        self.tabview = ctk.CTkTabview(
            self,
            corner_radius=12,
            fg_color=COLOR_CARD_DARK,
            segmented_button_selected_color=UA_BLUE,
            segmented_button_selected_hover_color=UA_BLUE_HOVER
        )
        self.tabview.grid(row=2, column=0, padx=16, pady=4, sticky="nsew")

        self.tab_controls = self.tabview.add(self.t('tab_controls'))
        self.tab_tweaks = self.tabview.add(self.t('tab_tweaks'))
        self.tab_saves = self.tabview.add(self.t('tab_saves'))
        self.tab_mods = self.tabview.add(self.t('tab_mods'))
        self.tab_radio = self.tabview.add(self.t('tab_radio'))
        self.tab_logs = self.tabview.add(self.t('tab_logs'))
        self.tab_clean_mods = self.tabview.add(self.t('tab_clean_mods'))
        self.tab_settings = self.tabview.add(self.t('tab_settings'))

        self._build_tab_controls()
        self._build_tab_tweaks()
        self._build_tab_saves()
        self._build_tab_mods()
        self._build_tab_radio()
        self._build_tab_logs()
        self._build_tab_clean_mods()
        self._build_tab_settings()

        # 4. Підвал (Footer)
        self.footer = ctk.CTkFrame(self, height=36, corner_radius=10, fg_color="#18191D")
        self.footer.grid(row=3, column=0, padx=16, pady=(4, 10), sticky="ew")
        self.footer.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(
            self.footer,
            text=self.t('ready'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#10B981",
            anchor="w"
        )
        self.lbl_status.grid(row=0, column=0, padx=14, pady=6, sticky="w")

        self.lbl_steam_sales = ctk.CTkLabel(
            self.footer,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=UA_YELLOW,
            cursor="hand2"
        )
        self.lbl_steam_sales.grid(row=0, column=1, padx=14, pady=6, sticky="e")
        self.lbl_steam_sales.bind("<Button-1>", lambda e: self.open_steam_link())

    # --- 1. ВКЛАДКА КЕРУВАННЯ ---
    def _build_tab_controls(self):
        tab = self.tab_controls
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        self.lbl_prof_hint = ctk.CTkLabel(
            tab,
            text=self.t('profile_hint'),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_prof_hint.grid(row=1, column=0, padx=14, pady=(10, 6), sticky="w")

        self.presets_frame = ctk.CTkScrollableFrame(
            tab,
            corner_radius=10,
            fg_color="#18191D",
            label_text=self.t('presets_label'),
            label_font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            label_text_color=UA_YELLOW
        )
        self.presets_frame.grid(row=2, column=0, padx=12, pady=4, sticky="nsew")
        self.presets_frame.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=3, column=0, padx=12, pady=10, sticky="ew")
        actions.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.btn_apply_preset = ctk.CTkButton(
            actions,
            text=self.t('btn_apply'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=40,
            fg_color=UA_BLUE,
            hover_color=UA_BLUE_HOVER,
            command=self.apply_selected_preset
        )
        self.btn_apply_preset.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_save_preset = ctk.CTkButton(
            actions,
            text=self.t('btn_save'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=40,
            fg_color=UA_YELLOW,
            hover_color=UA_YELLOW_HOVER,
            text_color=UA_YELLOW_TEXT_DARK,
            command=self.save_new_preset
        )
        self.btn_save_preset.grid(row=0, column=1, padx=4, sticky="ew")

        self.btn_quick_backup = ctk.CTkButton(
            actions,
            text=self.t('btn_backup'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=40,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.create_quick_backup
        )
        self.btn_quick_backup.grid(row=0, column=2, padx=4, sticky="ew")

        self.btn_open_preset_dir = ctk.CTkButton(
            actions,
            text=self.t('btn_open_presets'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=40,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.open_presets_directory
        )
        self.btn_open_preset_dir.grid(row=0, column=3, padx=4, sticky="ew")

        self.btn_import_preset = ctk.CTkButton(
            actions,
            text=self.t('btn_import_preset'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=40,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.import_preset_action
        )
        self.btn_import_preset.grid(row=0, column=4, padx=4, sticky="ew")

    # --- 2. ВКЛАДКА ТВІКІВ ГЕЙМПЛЕЮ ТА КАМЕРИ ---
    def _build_tab_tweaks(self):
        tab = self.tab_tweaks

        # Використовуємо CTkScrollableFrame, щоб весь вміст зручно прокручувався коліщатком миші
        # і жоден елемент, повзунок або підказка ніколи не обрізалися при будь-якому масштабі екрана!
        self.tweaks_scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.tweaks_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self.tweaks_scroll.grid_columnconfigure(0, weight=1)
        parent = self.tweaks_scroll

        # 1. Блок Консолі та HUD
        con_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        con_card.grid(row=0, column=0, padx=10, pady=6, sticky="ew")
        con_card.grid_columnconfigure(1, weight=1)

        self.lbl_con_head = ctk.CTkLabel(
            con_card,
            text=f"⚙️ {self.t('tweak_card_console')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_con_head.grid(row=0, column=0, padx=14, pady=(10, 4), sticky="w")

        self.lbl_con_status = ctk.CTkLabel(
            con_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_con_status.grid(row=0, column=1, padx=14, pady=(10, 4), sticky="e")

        btn_con_box = ctk.CTkFrame(con_card, fg_color="transparent")
        btn_con_box.grid(row=1, column=0, columnspan=2, padx=14, pady=4, sticky="w")

        self.btn_con_on = ctk.CTkButton(
            btn_con_box,
            text=self.t('btn_console_on'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=32,
            width=210,
            fg_color=UA_BLUE,
            hover_color=UA_BLUE_HOVER,
            command=lambda: self.set_console_state(True)
        )
        self.btn_con_on.pack(side="left", padx=(0, 10))

        self.btn_con_off = ctk.CTkButton(
            btn_con_box,
            text=self.t('btn_console_off'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=32,
            width=210,
            fg_color=COLOR_DANGER,
            hover_color=COLOR_DANGER_HOVER,
            command=lambda: self.set_console_state(False)
        )
        self.btn_con_off.pack(side="left")

        self.sw_minicon = ctk.CTkSwitch(
            con_card,
            text=self.t('tweak_minicon'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT
        )
        self.sw_minicon.grid(row=2, column=0, columnspan=2, padx=14, pady=(6, 12), sticky="w")

        # 2. Блок Трафіку, Вільної камери та FPS
        traffic_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        traffic_card.grid(row=1, column=0, padx=10, pady=6, sticky="ew")
        traffic_card.grid_columnconfigure(1, weight=1)

        self.lbl_card_traffic = ctk.CTkLabel(
            traffic_card,
            text=f"🚦 {self.t('tweak_card_traffic')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_card_traffic.grid(row=0, column=0, columnspan=3, padx=14, pady=(10, 4), sticky="w")

        # Трафік
        self.lbl_traffic_name = ctk.CTkLabel(
            traffic_card,
            text=self.t('traffic_label'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_traffic_name.grid(row=1, column=0, padx=14, pady=4, sticky="w")

        self.slider_traffic = ctk.CTkSlider(
            traffic_card,
            from_=0.0,
            to=10.0,
            number_of_steps=100,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_traffic_slider
        )
        self.slider_traffic.grid(row=1, column=1, padx=10, pady=4, sticky="ew")

        self.lbl_traffic_val = ctk.CTkLabel(
            traffic_card,
            text="1.0",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_traffic_val.grid(row=1, column=2, padx=14, pady=4)

        # Швидкість вільної камери
        self.lbl_fly_name = ctk.CTkLabel(
            traffic_card,
            text=self.t('flyspeed_label'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_fly_name.grid(row=2, column=0, padx=14, pady=4, sticky="w")

        self.slider_flyspeed = ctk.CTkSlider(
            traffic_card,
            from_=10,
            to=300,
            number_of_steps=58,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_flyspeed_slider
        )
        self.slider_flyspeed.grid(row=2, column=1, padx=10, pady=4, sticky="ew")

        self.lbl_flyspeed_val = ctk.CTkLabel(
            traffic_card,
            text="100",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_flyspeed_val.grid(row=2, column=2, padx=14, pady=4)

        # FPS Limiter
        self.lbl_fps_name = ctk.CTkLabel(
            traffic_card,
            text=self.t('fps_label'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_fps_name.grid(row=3, column=0, padx=14, pady=(4, 12), sticky="w")

        self.cb_fps = ctk.CTkComboBox(
            traffic_card,
            values=[f"0 ({self.t('fps_unlimited')})", "60", "75", "90", "120", "144", "165", "240"],
            state="readonly",
            button_color=UA_BLUE,
            button_hover_color=UA_BLUE_HOVER,
            width=190
        )
        self.cb_fps.grid(row=3, column=1, padx=10, pady=(4, 12), sticky="w")

        # 3. Блок Геймплейних правил та помічників
        rules_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        rules_card.grid(row=2, column=0, padx=10, pady=6, sticky="ew")

        self.lbl_card_rules = ctk.CTkLabel(
            rules_card,
            text=f"⚖️ {self.t('tweak_card_rules')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_card_rules.pack(anchor="w", padx=14, pady=(10, 6))

        self.sw_police = ctk.CTkSwitch(
            rules_card,
            text=self.t('tweak_police'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT
        )
        self.sw_police.pack(anchor="w", padx=14, pady=4)

        self.sw_fatigue = ctk.CTkSwitch(
            rules_card,
            text=self.t('tweak_fatigue'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT
        )
        self.sw_fatigue.pack(anchor="w", padx=14, pady=4)

        self.sw_steering_wheel = ctk.CTkSwitch(
            rules_card,
            text=self.t('tweak_steering_wheel'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT
        )
        self.sw_steering_wheel.pack(anchor="w", padx=14, pady=4)

        econ_box = ctk.CTkFrame(rules_card, fg_color="transparent")
        econ_box.pack(fill="x", padx=14, pady=(8, 12))

        self.btn_reset_economy = ctk.CTkButton(
            econ_box,
            text=self.t('btn_reset_economy'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=32,
            fg_color=UA_BLUE,
            hover_color=UA_BLUE_HOVER,
            command=self.reset_freight_economy
        )
        self.btn_reset_economy.pack(side="left", padx=(0, 10))

        self.lbl_reset_economy_hint = ctk.CTkLabel(
            econ_box,
            text=self.t('btn_reset_economy_hint'),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_reset_economy_hint.pack(side="left", fill="x", expand=True)

        # 4. Блок Фізики кабіни, тягача та причепа
        phys_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        phys_card.grid(row=3, column=0, padx=10, pady=6, sticky="ew")
        phys_card.grid_columnconfigure(1, weight=1)

        self.lbl_card_physics = ctk.CTkLabel(
            phys_card,
            text=f"🚚 {self.t('tweak_card_physics')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_card_physics.grid(row=0, column=0, columnspan=3, padx=14, pady=(10, 4), sticky="w")

        # Фізика камери
        self.lbl_cam_physics_name = ctk.CTkLabel(
            phys_card,
            text=self.t('tweak_cam_physics'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.lbl_cam_physics_name.grid(row=1, column=0, padx=14, pady=4, sticky="w")

        self.slider_cam_physics = ctk.CTkSlider(
            phys_card,
            from_=0.0,
            to=2.0,
            number_of_steps=20,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_cam_physics_slider
        )
        self.slider_cam_physics.grid(row=1, column=1, padx=10, pady=4, sticky="ew")

        self.lbl_cam_physics_val = ctk.CTkLabel(
            phys_card,
            text="1.0",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_cam_physics_val.grid(row=1, column=2, padx=14, pady=4)

        # Підвіска кабіни
        self.lbl_cabin_stiffness_name = ctk.CTkLabel(
            phys_card,
            text=self.t('tweak_cabin_stiffness'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.lbl_cabin_stiffness_name.grid(row=2, column=0, padx=14, pady=4, sticky="w")

        self.slider_cabin_stiffness = ctk.CTkSlider(
            phys_card,
            from_=0.0,
            to=1.0,
            number_of_steps=20,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_cabin_stiffness_slider
        )
        self.slider_cabin_stiffness.grid(row=2, column=1, padx=10, pady=4, sticky="ew")

        self.lbl_cabin_stiffness_val = ctk.CTkLabel(
            phys_card,
            text="1.00",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_cabin_stiffness_val.grid(row=2, column=2, padx=14, pady=4)

        # Стабільність тягача
        self.lbl_truck_stability_name = ctk.CTkLabel(
            phys_card,
            text=self.t('tweak_truck_stability'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.lbl_truck_stability_name.grid(row=3, column=0, padx=14, pady=4, sticky="w")

        self.slider_truck_stability = ctk.CTkSlider(
            phys_card,
            from_=0.0,
            to=1.0,
            number_of_steps=20,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_truck_stability_slider
        )
        self.slider_truck_stability.grid(row=3, column=1, padx=10, pady=4, sticky="ew")

        self.lbl_truck_stability_val = ctk.CTkLabel(
            phys_card,
            text="0.50",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_truck_stability_val.grid(row=3, column=2, padx=14, pady=4)

        # Стабільність причепа
        self.lbl_trailer_stability_name = ctk.CTkLabel(
            phys_card,
            text=self.t('tweak_trailer_stability'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.lbl_trailer_stability_name.grid(row=4, column=0, padx=14, pady=4, sticky="w")

        self.slider_trailer_stability = ctk.CTkSlider(
            phys_card,
            from_=0.0,
            to=1.0,
            number_of_steps=20,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_trailer_stability_slider
        )
        self.slider_trailer_stability.grid(row=4, column=1, padx=10, pady=4, sticky="ew")

        self.lbl_trailer_stability_val = ctk.CTkLabel(
            phys_card,
            text="0.50",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_trailer_stability_val.grid(row=4, column=2, padx=14, pady=4)

        # Гальма
        self.lbl_brake_intensity_name = ctk.CTkLabel(
            phys_card,
            text=self.t('tweak_brake_intensity'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.lbl_brake_intensity_name.grid(row=5, column=0, padx=14, pady=(4, 12), sticky="w")

        self.slider_brake_intensity = ctk.CTkSlider(
            phys_card,
            from_=0.1,
            to=2.0,
            number_of_steps=19,
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT,
            command=self._on_brake_intensity_slider
        )
        self.slider_brake_intensity.grid(row=5, column=1, padx=10, pady=(4, 12), sticky="ew")

        self.lbl_brake_intensity_val = ctk.CTkLabel(
            phys_card,
            text="1.00",
            width=60,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_brake_intensity_val.grid(row=5, column=2, padx=14, pady=(4, 12))

        # 5. Блок Оптимізації графіки та FPS
        opt_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        opt_card.grid(row=4, column=0, padx=10, pady=6, sticky="ew")

        self.lbl_card_graphics = ctk.CTkLabel(
            opt_card,
            text=f"⚡ {self.t('tweak_card_graphics')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_card_graphics.pack(anchor="w", padx=14, pady=(10, 6))

        self.sw_pedestrian = ctk.CTkSwitch(
            opt_card,
            text=self.t('tweak_pedestrian'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT
        )
        self.sw_pedestrian.pack(anchor="w", padx=14, pady=4)

        self.sw_cloud_shadows = ctk.CTkSwitch(
            opt_card,
            text=self.t('tweak_cloud_shadows'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            progress_color=UA_BLUE,
            button_color=UA_YELLOW,
            button_hover_color=UA_YELLOW_LIGHT
        )
        self.sw_cloud_shadows.pack(anchor="w", padx=14, pady=(4, 12))

        # 6. Кнопки збереження та скидання
        action_card = ctk.CTkFrame(parent, fg_color="transparent")
        action_card.grid(row=5, column=0, padx=10, pady=10, sticky="ew")
        action_card.grid_columnconfigure(0, weight=2)
        action_card.grid_columnconfigure(1, weight=1)

        self.btn_save_tweaks = ctk.CTkButton(
            action_card,
            text=self.t('btn_save_tweaks'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=38,
            fg_color=UA_YELLOW,
            hover_color=UA_YELLOW_HOVER,
            text_color=UA_YELLOW_TEXT_DARK,
            command=self.save_gameplay_tweaks
        )
        self.btn_save_tweaks.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        self.btn_restore_defaults = ctk.CTkButton(
            action_card,
            text=self.t('btn_restore_default_tweaks'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=38,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.restore_default_tweaks
        )
        self.btn_restore_defaults.grid(row=0, column=1, sticky="ew")

        # 7. Розширена Шпаргалка (Cheat Sheet)
        cheat_card = ctk.CTkFrame(parent, corner_radius=10, fg_color="#18191D")
        cheat_card.grid(row=6, column=0, padx=10, pady=(6, 16), sticky="ew")

        self.lbl_cheat_title = ctk.CTkLabel(
            cheat_card,
            text=self.t('cheat_sheet_title'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_cheat_title.pack(anchor="w", padx=16, pady=(12, 4))

        self.lbl_cheat_text = ctk.CTkLabel(
            cheat_card,
            text=self.t('console_cheat_sheet'),
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLOR_TEXT_MUTED,
            justify="left"
        )
        self.lbl_cheat_text.pack(anchor="w", padx=16, pady=(0, 14))

    def _on_traffic_slider(self, val):
        self.lbl_traffic_val.configure(text=f"{val:.1f}")

    def _on_flyspeed_slider(self, val):
        self.lbl_flyspeed_val.configure(text=f"{int(val)}")

    def _on_cam_physics_slider(self, val):
        self.lbl_cam_physics_val.configure(text=f"{val:.1f}")

    def _on_cabin_stiffness_slider(self, val):
        self.lbl_cabin_stiffness_val.configure(text=f"{val:.2f}")

    def _on_truck_stability_slider(self, val):
        self.lbl_truck_stability_val.configure(text=f"{val:.2f}")

    def _on_trailer_stability_slider(self, val):
        self.lbl_trailer_stability_val.configure(text=f"{val:.2f}")

    def _on_brake_intensity_slider(self, val):
        self.lbl_brake_intensity_val.configure(text=f"{val:.2f}")

    # --- 3. ВКЛАДКА БЕКАПІВ СЕЙВІВ ---
    PROFILE_STATS = (
        ("experience", "stat_experience"), ("distance", "stat_distance"),
        ("money", "stat_money"), ("mods", "stat_mods"),
        ("cities", "stat_cities"), ("explored", "stat_explored"),
        ("slots", "stat_slots"), ("last_save", "stat_last_save"),
    )

    def _build_tab_saves(self):
        tab = self.tab_saves
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        # Картка профілю: дані з profile.sii та info.sii останнього сейву
        prof_card = ctk.CTkFrame(tab, corner_radius=10, fg_color=COLOR_CARD_INNER)
        prof_card.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        prof_card.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")

        self.lbl_profile_head = ctk.CTkLabel(
            prof_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_profile_head.grid(row=0, column=0, columnspan=4, padx=14, pady=(10, 0), sticky="w")

        self.lbl_profile_sub = ctk.CTkLabel(
            prof_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_profile_sub.grid(row=1, column=0, columnspan=4, padx=14, pady=(0, 6), sticky="w")

        self.profile_stat_labels = {}
        for i, (key, caption_key) in enumerate(self.PROFILE_STATS):
            tile = ctk.CTkFrame(prof_card, corner_radius=8, fg_color="#1D1F24")
            tile.grid(row=2 + i // 4, column=i % 4, padx=(14 if i % 4 == 0 else 4, 14 if i % 4 == 3 else 4),
                      pady=(0, 8 if i // 4 else 6), sticky="ew")
            val = ctk.CTkLabel(tile, text="—", font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
                               text_color=COLOR_TEXT_WHITE)
            val.pack(anchor="w", padx=10, pady=(6, 0))
            cap = ctk.CTkLabel(tile, text=self.t(caption_key), font=ctk.CTkFont(family="Segoe UI", size=11),
                               text_color=COLOR_TEXT_MUTED)
            cap.pack(anchor="w", padx=10, pady=(0, 6))
            self.profile_stat_labels[key] = (val, cap, caption_key)

        # Перемикач: слоти сейвів / ZIP-архіви
        switch_row = ctk.CTkFrame(tab, fg_color="transparent")
        switch_row.grid(row=1, column=0, padx=12, pady=(2, 4), sticky="ew")
        switch_row.grid_columnconfigure(1, weight=1)

        self.seg_saves_view = ctk.CTkSegmentedButton(
            switch_row,
            values=[self.t('saves_view_slots'), self.t('saves_view_zip')],
            command=lambda _v: self._show_saves_panel(),
            selected_color=UA_BLUE,
            selected_hover_color=UA_BLUE_HOVER,
            height=34
        )
        self.seg_saves_view.set(self.t('saves_view_slots'))
        self.seg_saves_view.grid(row=0, column=0, sticky="w")

        self.lbl_saves_hint = ctk.CTkLabel(
            switch_row,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_saves_hint.grid(row=0, column=1, padx=12, sticky="w")

        self.btn_create_save = ctk.CTkButton(
            switch_row,
            text=self.t('btn_create_save_zip'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=34,
            fg_color=COLOR_SUCCESS,
            hover_color=COLOR_SUCCESS_HOVER,
            command=self.create_save_zip_backup
        )
        self.btn_create_save.grid(row=0, column=2, sticky="e")

        # Панель слотів сейвів
        self.slots_frame = ctk.CTkScrollableFrame(tab, corner_radius=10, fg_color="#18191D")
        self.slots_frame.grid(row=2, column=0, padx=12, pady=(4, 12), sticky="nsew")
        self.slots_frame.grid_columnconfigure(0, weight=1)

        # Панель ZIP-архівів
        self.zip_panel = ctk.CTkFrame(tab, fg_color="transparent")
        self.zip_panel.grid(row=2, column=0, padx=0, pady=0, sticky="nsew")
        self.zip_panel.grid_columnconfigure(0, weight=1)
        self.zip_panel.grid_rowconfigure(0, weight=1)

        self.saves_frame = ctk.CTkScrollableFrame(self.zip_panel, corner_radius=10, fg_color="#18191D")
        self.saves_frame.grid(row=0, column=0, padx=12, pady=(4, 6), sticky="nsew")
        self.saves_frame.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(self.zip_panel, fg_color="transparent")
        actions.grid(row=1, column=0, padx=12, pady=(4, 12), sticky="ew")
        actions.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_restore_save = ctk.CTkButton(
            actions,
            text=self.t('btn_restore_save'),
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=38,
            fg_color=UA_BLUE,
            hover_color=UA_BLUE_HOVER,
            command=self.restore_selected_save_archive
        )
        self.btn_restore_save.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_delete_save = ctk.CTkButton(
            actions,
            text=self.t('btn_delete_save_zip'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=38,
            fg_color=COLOR_DANGER,
            hover_color=COLOR_DANGER_HOVER,
            command=self.delete_selected_save_archive
        )
        self.btn_delete_save.grid(row=0, column=1, padx=4, sticky="ew")

        self.btn_open_save_dir = ctk.CTkButton(
            actions,
            text=self.t('btn_open_save_folder'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=38,
            fg_color="#374151",
            hover_color="#4B5563",
            command=lambda: self._open_app_folder(self.get_saves_backup_path)
        )
        self.btn_open_save_dir.grid(row=0, column=2, padx=4, sticky="ew")

        self._show_saves_panel()

    def _saves_view_index(self) -> int:
        values = list(self.seg_saves_view.cget("values"))
        return values.index(self.seg_saves_view.get()) if self.seg_saves_view.get() in values else 0

    def _show_saves_panel(self):
        if self._saves_view_index() == 0:
            self.zip_panel.grid_remove()
            self.slots_frame.grid()
            self.lbl_saves_hint.configure(text=self.t('saves_slots_hint'))
        else:
            self.slots_frame.grid_remove()
            self.zip_panel.grid()
            self.lbl_saves_hint.configure(text=self.t('saves_desc'))

    # --- 4. ВКЛАДКА ЛОГІВ ТА АНАЛІЗУ ВИЛЬОТІВ ---
    def _build_tab_logs(self):
        tab = self.tab_logs
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        top_card = ctk.CTkFrame(tab, corner_radius=10, fg_color=COLOR_CARD_INNER)
        top_card.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        top_card.grid_columnconfigure(0, weight=1)

        self.lbl_log_title = ctk.CTkLabel(
            top_card,
            text=f"🔍 {self.t('log_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_log_title.grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        self.lbl_log_stats = ctk.CTkLabel(
            top_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=COLOR_TEXT_WHITE
        )
        self.lbl_log_stats.grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 4), sticky="w")

        # Зведення останнього збою з game.crash.txt
        self.lbl_crash_summary = ctk.CTkLabel(
            top_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#D1D5DB",
            justify="left",
            anchor="w"
        )
        self.lbl_crash_summary.grid(row=2, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="w")

        # Кнопки окремим рядком, щоб не обрізати заголовок
        btn_box = ctk.CTkFrame(top_card, fg_color="transparent")
        btn_box.grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")

        self.btn_refresh_log = ctk.CTkButton(
            btn_box,
            text=self.t('btn_refresh_log'),
            height=32,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.refresh_log_analyzer
        )
        self.btn_refresh_log.pack(side="left", padx=4)

        self.btn_open_log = ctk.CTkButton(
            btn_box,
            text=self.t('btn_open_log_file'),
            height=32,
            fg_color=UA_BLUE,
            hover_color=UA_BLUE_HOVER,
            command=self.open_game_log_in_editor
        )
        self.btn_open_log.pack(side="left", padx=4)

        self.btn_open_crash = ctk.CTkButton(
            btn_box,
            text=self.t('btn_open_crash_file'),
            height=32,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.open_crash_log_in_editor
        )
        self.btn_open_crash.pack(side="left", padx=4)

        self.btn_net_check = ctk.CTkButton(
            btn_box,
            text=self.t('btn_net_check'),
            height=32,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.run_net_check
        )
        self.btn_net_check.pack(side="left", padx=4)

        self.btn_copy_report = ctk.CTkButton(
            btn_box,
            text=self.t('btn_copy_report'),
            height=32,
            fg_color=UA_YELLOW,
            hover_color=UA_YELLOW_HOVER,
            text_color=UA_YELLOW_TEXT_DARK,
            command=self.copy_diagnostic_report
        )
        self.btn_copy_report.pack(side="left", padx=4)
        Tooltip(self.btn_copy_report, self.t('report_tip'))

        # Вид + пошук
        filter_row = ctk.CTkFrame(tab, fg_color="transparent")
        filter_row.grid(row=1, column=0, padx=12, pady=4, sticky="ew")
        filter_row.grid_columnconfigure(1, weight=1)

        self.seg_log_view = ctk.CTkSegmentedButton(
            filter_row,
            values=[self.t('log_view_groups'), self.t('log_view_errors'), self.t('log_view_warnings'),
                    self.t('log_view_crashes'), self.t('log_view_network')],
            command=lambda _v: self.filter_log_view(),
            selected_color=UA_BLUE,
            selected_hover_color=UA_BLUE_HOVER,
            height=34
        )
        self.seg_log_view.set(self.t('log_view_groups'))
        self.seg_log_view.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.entry_log_filter = ctk.CTkEntry(
            filter_row,
            placeholder_text=self.t('log_search_placeholder'),
            height=34,
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.entry_log_filter.grid(row=0, column=1, sticky="ew")
        self.entry_log_filter.bind("<KeyRelease>", lambda e: self.filter_log_view())

        # Текстове поле
        self.txt_log_errors = ctk.CTkTextbox(
            tab,
            corner_radius=10,
            fg_color="#18191D",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#D1D5DB",
            wrap="none"
        )
        self.txt_log_errors.grid(row=3, column=0, padx=12, pady=(4, 12), sticky="nsew")
        self.txt_log_errors.tag_config("head_mod", foreground=UA_YELLOW)
        self.txt_log_errors.tag_config("head_game", foreground=UA_BLUE_LIGHT)
        self.txt_log_errors.tag_config("err", foreground="#F87171")
        self.txt_log_errors.tag_config("warn", foreground="#FBBF24")
        self.txt_log_errors.tag_config("muted", foreground=COLOR_TEXT_MUTED)
        self.txt_log_errors.tag_config("ok", foreground="#D1D5DB")
        self.txt_log_errors.tag_config("link", foreground=UA_BLUE_LIGHT, underline=True)

    # --- 5. ВКЛАДКА ОЧИЩЕННЯ ТА МОДІВ ---
    def _build_tab_clean_mods(self):
        tab = self.tab_clean_mods
        self.clean_scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.clean_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self.clean_scroll.grid_columnconfigure(0, weight=1)
        parent = self.clean_scroll

        # Картка кешу
        cache_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        cache_card.grid(row=0, column=0, padx=10, pady=(10, 8), sticky="ew")

        ctk.CTkLabel(
            cache_card,
            text=f"🧹 {self.t('clean_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        ).pack(anchor="w", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            cache_card,
            text=self.t('clean_desc'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#D1D5DB",
            justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.lbl_cache_size = ctk.CTkLabel(
            cache_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_cache_size.pack(anchor="w", padx=16, pady=(0, 10))

        btn_cache_box = ctk.CTkFrame(cache_card, fg_color="transparent")
        btn_cache_box.pack(anchor="w", padx=16, pady=(0, 14))

        self.btn_clean_current = ctk.CTkButton(
            btn_cache_box,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=34,
            width=240,
            fg_color=UA_YELLOW,
            hover_color=UA_YELLOW_HOVER,
            text_color=UA_YELLOW_TEXT_DARK,
            command=self.clean_cache_current
        )
        self.btn_clean_current.pack(side="left", padx=(0, 10))

        self.btn_clean_both = ctk.CTkButton(
            btn_cache_box,
            text=self.t('clean_both_btn'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=34,
            width=240,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.clean_cache_both
        )
        self.btn_clean_both.pack(side="left")

        # Картка автоматичних бекапів гри: steam_profiles(версія).bak
        bak_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        bak_card.grid(row=1, column=0, padx=10, pady=8, sticky="ew")

        self.lbl_bak_title = ctk.CTkLabel(
            bak_card,
            text=f"🗂 {self.t('bak_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_bak_title.pack(anchor="w", padx=16, pady=(12, 4))

        self.lbl_bak_desc = ctk.CTkLabel(
            bak_card,
            text=self.t('bak_desc'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#D1D5DB",
            justify="left",
            wraplength=820
        )
        self.lbl_bak_desc.pack(anchor="w", padx=16, pady=(0, 6))

        self.lbl_bak_summary = ctk.CTkLabel(
            bak_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_bak_summary.pack(anchor="w", padx=16, pady=(0, 2))

        self.lbl_bak_list = ctk.CTkLabel(
            bak_card,
            text="",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLOR_TEXT_MUTED,
            justify="left"
        )
        self.lbl_bak_list.pack(anchor="w", padx=16, pady=(0, 8))

        bak_box = ctk.CTkFrame(bak_card, fg_color="transparent")
        bak_box.pack(anchor="w", padx=16, pady=(0, 14))

        self.lbl_bak_keep = ctk.CTkLabel(bak_box, text=self.t('bak_keep'), font=ctk.CTkFont(family="Segoe UI", size=12))
        self.lbl_bak_keep.pack(side="left", padx=(0, 8))

        self.cb_bak_keep = ctk.CTkOptionMenu(
            bak_box, values=["1", "2", "3", "5"], width=70, height=32,
            fg_color="#374151", button_color="#4B5563", button_hover_color=UA_BLUE_HOVER,
            command=lambda _v: self.refresh_cleanup_info()
        )
        self.cb_bak_keep.set("2")
        self.cb_bak_keep.pack(side="left", padx=(0, 12))

        self.btn_bak_clean = ctk.CTkButton(
            bak_box,
            text=self.t('bak_clean_btn'),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            height=32,
            fg_color=UA_YELLOW,
            hover_color=UA_YELLOW_HOVER,
            text_color=UA_YELLOW_TEXT_DARK,
            command=self.clean_game_auto_backups
        )
        self.btn_bak_clean.pack(side="left")

        # Картка скриншотів
        shot_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        shot_card.grid(row=2, column=0, padx=10, pady=8, sticky="ew")

        self.lbl_shots_title = ctk.CTkLabel(
            shot_card,
            text=f"📸 {self.t('shots_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_shots_title.pack(anchor="w", padx=16, pady=(12, 4))

        self.lbl_shots_info = ctk.CTkLabel(
            shot_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#D1D5DB",
            justify="left"
        )
        self.lbl_shots_info.pack(anchor="w", padx=16, pady=(0, 8))

        self.btn_shots_open = ctk.CTkButton(
            shot_card,
            text=self.t('shots_open'),
            height=32,
            width=200,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self.open_screenshots_folder
        )
        self.btn_shots_open.pack(anchor="w", padx=16, pady=(0, 14))


    # --- ВКЛАДКА МОДІВ ---
    def _build_tab_mods(self):
        tab = self.tab_mods
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        mods_card = ctk.CTkFrame(tab, corner_radius=10, fg_color=COLOR_CARD_INNER)
        mods_card.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        mods_card.grid_columnconfigure(0, weight=1)

        self.lbl_mods_title = ctk.CTkLabel(
            mods_card,
            text=f"📦 {self.t('mods_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_mods_title.grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        btn_box = ctk.CTkFrame(mods_card, fg_color="transparent")
        btn_box.grid(row=0, column=1, padx=14, pady=(10, 2), sticky="e")

        self.btn_mods_refresh = ctk.CTkButton(
            btn_box, text=self.t('btn_mods_refresh'), height=32,
            fg_color="#374151", hover_color="#4B5563",
            command=self.refresh_mods_diag
        )
        self.btn_mods_refresh.pack(side="left", padx=4)

        self.btn_open_mods = ctk.CTkButton(
            btn_box, text=self.t('btn_open_mods'), height=32,
            fg_color="#374151", hover_color="#4B5563",
            command=self.open_mods_folder
        )
        self.btn_open_mods.pack(side="left", padx=4)

        self.btn_workshop_subs = ctk.CTkButton(
            btn_box, text=self.t('btn_workshop_subs'), height=32,
            fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER,
            command=self.open_workshop_subscriptions
        )
        self.btn_workshop_subs.pack(side="left", padx=4)

        self.lbl_mods_info = ctk.CTkLabel(
            mods_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED,
            justify="left"
        )
        self.lbl_mods_info.grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 2), sticky="w")

        self.lbl_mods_summary = ctk.CTkLabel(
            mods_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLOR_TEXT_WHITE,
            justify="left"
        )
        self.lbl_mods_summary.grid(row=2, column=0, columnspan=2, padx=14, pady=(0, 10), sticky="w")

        filter_row = ctk.CTkFrame(tab, fg_color="transparent")
        filter_row.grid(row=1, column=0, padx=12, pady=4, sticky="ew")
        filter_row.grid_columnconfigure(1, weight=1)

        self.seg_mods_view = ctk.CTkSegmentedButton(
            filter_row,
            values=[self.t('mods_view_order'), self.t('mods_view_problems'), self.t('mods_view_unused')],
            command=lambda _v: self.render_mods_view(),
            selected_color=UA_BLUE,
            selected_hover_color=UA_BLUE_HOVER,
            height=34
        )
        self.seg_mods_view.set(self.t('mods_view_order'))
        self.seg_mods_view.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.entry_mods_filter = ctk.CTkEntry(
            filter_row,
            placeholder_text=self.t('mods_search_placeholder'),
            height=34,
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.entry_mods_filter.grid(row=0, column=1, sticky="ew")
        self.entry_mods_filter.bind("<KeyRelease>", lambda e: self.render_mods_view())

        self.btn_mods_sort = ctk.CTkButton(
            filter_row, text=self.t('btn_mods_sort'), height=34,
            fg_color=UA_YELLOW, hover_color=UA_YELLOW_HOVER, text_color=UA_YELLOW_TEXT_DARK,
            command=self.mods_auto_sort
        )
        self.btn_mods_sort.grid(row=0, column=2, padx=(8, 0))
        Tooltip(self.btn_mods_sort, self.t('modsort_tip'))

        self.btn_mods_sort_undo = ctk.CTkButton(
            filter_row, text=self.t('btn_mods_sort_undo'), height=34, width=110,
            fg_color="#374151", hover_color="#4B5563",
            command=self.mods_sort_undo
        )
        self.btn_mods_sort_undo.grid(row=0, column=3, padx=(8, 0))
        if not getattr(self, '_mod_sort_undo', None):
            self.btn_mods_sort_undo.grid_remove()

        self.txt_mods = ctk.CTkTextbox(
            tab,
            corner_radius=10,
            fg_color="#18191D",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color="#D1D5DB",
            wrap="none"
        )
        self.txt_mods.grid(row=2, column=0, padx=12, pady=(4, 12), sticky="nsew")
        self.txt_mods.tag_config("head", foreground=UA_YELLOW)
        self.txt_mods.tag_config("err", foreground="#F87171")
        self.txt_mods.tag_config("warn", foreground="#FBBF24")
        self.txt_mods.tag_config("ok", foreground="#D1D5DB")
        self.txt_mods.tag_config("muted", foreground=COLOR_TEXT_MUTED)

    # --- ВКЛАДКА РАДІО ---
    def _build_tab_radio(self):
        from tkinter import ttk
        tab = self.tab_radio
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        card = ctk.CTkFrame(tab, corner_radius=10, fg_color=COLOR_CARD_INNER)
        card.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        self.lbl_radio_title = ctk.CTkLabel(
            card, text=f"📻 {self.t('radio_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"), text_color=UA_YELLOW)
        self.lbl_radio_title.grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        self.lbl_radio_summary = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLOR_TEXT_WHITE, justify="left")
        self.lbl_radio_summary.grid(row=1, column=0, columnspan=2, padx=14, sticky="w")

        self.lbl_radio_hint = ctk.CTkLabel(
            card, text=self.t('radio_hint'), font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED, justify="left", wraplength=900)
        self.lbl_radio_hint.grid(row=2, column=0, columnspan=2, padx=14, pady=(0, 8), sticky="w")

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")
        self.radio_buttons = {}
        for key, color, cmd in (
            ('btn_radio_search', UA_BLUE, self.radio_search_online),
            ('btn_radio_add', UA_BLUE, self.radio_add_station),
            ('btn_radio_fav', "#374151", self.radio_toggle_favorite),
            ('btn_radio_check', "#374151", self.radio_check_selected),
            ('btn_radio_check_all', "#374151", self.radio_check_all),
            ('btn_radio_delete', COLOR_DANGER, self.radio_delete_selected),
            ('btn_radio_reapply', "#374151", self.radio_reapply_mine),
        ):
            b = ctk.CTkButton(btns, text=self.t(key), height=30, fg_color=color,
                              hover_color=UA_BLUE_HOVER if color == UA_BLUE else (COLOR_DANGER_HOVER if color == COLOR_DANGER else "#4B5563"),
                              command=cmd)
            b.pack(side="left", padx=4)
            self.radio_buttons[key] = b

        filter_row = ctk.CTkFrame(tab, fg_color="transparent")
        filter_row.grid(row=1, column=0, padx=12, pady=4, sticky="ew")
        filter_row.grid_columnconfigure(0, weight=1)

        self.entry_radio_filter = ctk.CTkEntry(
            filter_row, placeholder_text=self.t('radio_search_placeholder'), height=34,
            font=ctk.CTkFont(family="Segoe UI", size=12))
        self.entry_radio_filter.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry_radio_filter.bind("<KeyRelease>", lambda e: self.render_radio_list())

        self.cb_radio_lang = ctk.CTkOptionMenu(
            filter_row, values=[self.t('radio_all_langs')], width=150, height=34,
            fg_color="#374151", button_color="#4B5563", button_hover_color=UA_BLUE_HOVER,
            command=lambda _v: self.render_radio_list())
        self.cb_radio_lang.grid(row=0, column=1)

        # Таблиця станцій: ttk.Treeview у темній темі (CTk-рядки для сотень станцій надто повільні)
        scale = self._get_window_scaling()
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Radio.Treeview", background="#18191D", fieldbackground="#18191D",
                        foreground="#D1D5DB", rowheight=int(26 * scale), borderwidth=0,
                        font=("Segoe UI", int(10 * scale)))
        style.configure("Radio.Treeview.Heading", background="#25272D", foreground=UA_YELLOW,
                        relief="flat", font=("Segoe UI", int(10 * scale), "bold"))
        style.map("Radio.Treeview", background=[("selected", UA_BLUE)], foreground=[("selected", "#FFFFFF")])
        style.map("Radio.Treeview.Heading", background=[("active", "#2D3039")])

        table = ctk.CTkFrame(tab, corner_radius=10, fg_color="#18191D")
        table.grid(row=2, column=0, padx=12, pady=(4, 12), sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)

        cols = ("fav", "name", "genre", "lang", "bitrate", "status", "url")
        self.radio_tree = ttk.Treeview(table, columns=cols, show="headings", style="Radio.Treeview", selectmode="extended")
        widths = {"fav": 34, "name": 220, "genre": 140, "lang": 50, "bitrate": 60, "status": 130, "url": 320}
        for c in cols:
            self.radio_tree.heading(c, text=self.t(f'radio_col_{c}'), command=lambda c=c: self._radio_sort(c))
            self.radio_tree.column(c, width=int(widths[c] * scale), stretch=c in ("name", "url"),
                                   anchor="center" if c in ("fav", "lang", "bitrate") else "w")
        self.radio_tree.tag_configure("mine", foreground=UA_YELLOW)
        self.radio_tree.tag_configure("dead", foreground="#F87171")
        self.radio_tree.tag_configure("alive", foreground="#34D399")
        self.radio_tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb = ctk.CTkScrollbar(table, command=self.radio_tree.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.radio_tree.configure(yscrollcommand=sb.set)
        self.radio_tree.bind("<Double-1>", lambda e: self.radio_toggle_favorite())
        self.radio_sort_key = None

    # --- 6. ВКЛАДКА НАЛАШТУВАНЬ ---
    def _build_tab_settings(self):
        tab = self.tab_settings
        self.settings_scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.settings_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self.settings_scroll.grid_columnconfigure(1, weight=1)
        parent = self.settings_scroll

        self.lbl_sett_title = ctk.CTkLabel(
            parent,
            text=f"📁 {self.t('settings_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=UA_YELLOW
        )
        self.lbl_sett_title.grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 4), sticky="w")

        self.lbl_sett_desc = ctk.CTkLabel(
            parent,
            text=self.t('settings_desc'),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLOR_TEXT_MUTED
        )
        self.lbl_sett_desc.grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 12), sticky="w")

        # ETS2
        ctk.CTkLabel(parent, text="ETS 2:", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")).grid(row=2, column=0, padx=16, pady=6, sticky="w")
        self.entry_ets2_path = ctk.CTkEntry(parent, font=ctk.CTkFont(family="Consolas", size=11))
        self.entry_ets2_path.grid(row=2, column=1, padx=6, pady=6, sticky="ew")
        ctk.CTkButton(parent, text=self.t('btn_change_path'), fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER, width=120, command=lambda: self.browse_specific_path("Euro Truck Simulator 2")).grid(row=2, column=2, padx=16, pady=6)

        # ATS
        ctk.CTkLabel(parent, text="ATS:", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")).grid(row=3, column=0, padx=16, pady=6, sticky="w")
        self.entry_ats_path = ctk.CTkEntry(parent, font=ctk.CTkFont(family="Consolas", size=11))
        self.entry_ats_path.grid(row=3, column=1, padx=6, pady=6, sticky="ew")
        ctk.CTkButton(parent, text=self.t('btn_change_path'), fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER, width=120, command=lambda: self.browse_specific_path("American Truck Simulator")).grid(row=3, column=2, padx=16, pady=6)

        # Шлях, введений вручну, зберігається по Enter або при виході з поля
        for entry, game in ((self.entry_ets2_path, "Euro Truck Simulator 2"),
                            (self.entry_ats_path, "American Truck Simulator")):
            entry.bind("<Return>", lambda e, en=entry, g=game: self.commit_typed_path(en, g))
            entry.bind("<FocusOut>", lambda e, en=entry, g=game: self.commit_typed_path(en, g))

        self.lbl_paths_src = ctk.CTkLabel(
            parent,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED,
            justify="left"
        )
        self.lbl_paths_src.grid(row=4, column=0, columnspan=3, padx=16, pady=(2, 0), sticky="w")

        btns_row = ctk.CTkFrame(parent, fg_color="transparent")
        btns_row.grid(row=5, column=0, columnspan=3, padx=16, pady=20, sticky="w")

        ctk.CTkButton(btns_row, text=self.t('btn_reset_paths'), fg_color="#374151", hover_color="#4B5563", command=self.reset_paths_to_default).pack(side="left", padx=(0, 12))
        ctk.CTkButton(btns_row, text=self.t('btn_open_logs'), fg_color="#374151", hover_color="#4B5563", command=self.open_logs_directory).pack(side="left")

        # Сервер конвою (server_config.sii)
        srv = ctk.CTkFrame(parent, corner_radius=10, fg_color=COLOR_CARD_INNER)
        srv.grid(row=6, column=0, columnspan=3, padx=16, pady=(4, 16), sticky="ew")
        self.lbl_server_title = ctk.CTkLabel(
            srv, text=f"🖧 {self.t('server_card_title')}",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"), text_color=UA_YELLOW)
        self.lbl_server_title.pack(anchor="w", padx=14, pady=(10, 2))
        self.lbl_server_info = ctk.CTkLabel(
            srv, text="", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=COLOR_TEXT_MUTED,
            justify="left", wraplength=820)
        self.lbl_server_info.pack(anchor="w", padx=14, pady=(0, 6))
        self.btn_server_edit = ctk.CTkButton(
            srv, text=self.t('server_btn'), height=32, fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER,
            command=self.edit_server_config)
        self.btn_server_edit.pack(anchor="w", padx=14, pady=(0, 12))

    # --- МОНІТОРИНГ ПРОЦЕСУ ГРИ ---
    def check_game_process_loop(self):
        running = is_game_running(self.current_game)
        if running:
            self.lbl_game_proc_status.configure(
                text=self.t('game_proc_running'),
                text_color="#EF4444"
            )
            self.btn_launch_game.configure(state="disabled")
        else:
            self.lbl_game_proc_status.configure(
                text=self.t('game_proc_closed'),
                text_color="#10B981"
            )
            self.btn_launch_game.configure(state="normal")
        self.after(2500, self.check_game_process_loop)

    def launch_game_via_steam(self):
        appid = STEAM_APPS.get(self.current_game, 227300)
        os.startfile(f"steam://rungameid/{appid}")
        self.set_status(self.t('msg_launching').format(self.current_game))

    # --- ОНОВЛЕННЯ ДАНИХ ТА ІНТЕРФЕЙСУ ---
    def refresh_all(self):
        active_path = self.get_active_game_path()

        self.entry_path.configure(state="normal")
        self.entry_path.delete(0, "end")
        self.entry_path.insert(0, str(active_path))
        self.entry_path.configure(state="readonly")

        self.entry_ets2_path.delete(0, "end")
        self.entry_ets2_path.insert(0, str(self.settings.get_game_path("Euro Truck Simulator 2")))
        self.entry_ats_path.delete(0, "end")
        self.entry_ats_path.insert(0, str(self.settings.get_game_path("American Truck Simulator")))
        self.lbl_paths_src.configure(text=self._paths_source_text())

        self.scan_profiles()
        self.refresh_profile_card()
        self.refresh_save_slots()
        self.refresh_presets_list()
        self.refresh_config_tweaks()
        self.refresh_saves_list()
        self.refresh_log_analyzer()
        self.refresh_cache_status()
        self.refresh_cleanup_info()
        self.refresh_mods_info()
        self.refresh_radio()
        self.refresh_server_info()

    def scan_profiles(self):
        # Запам'ятовуємо вибраний профіль, щоб оновлення/зміна мови не скидали вибір
        prev_path = self.get_selected_profile_path()
        self.profiles = []
        game_path = self.get_active_game_path()

        if game_path.exists():
            for folder_name in ('profiles', 'steam_profiles'):
                p_dir = game_path / folder_name
                if p_dir.exists():
                    for sub in sorted(p_dir.iterdir()):
                        if sub.is_dir():
                            cloud = None
                            if folder_name == 'steam_profiles':
                                cloud = find_steam_cloud_profile_dir(self.current_game, sub.name)
                            name = read_profile_name(sub, cloud)
                            self.profiles.append((sub.name, sub, name, folder_name, cloud))

        count = len(self.profiles)
        if count > 0:
            display_values = [
                f"{name}  [{'Steam Cloud' if cloud else src}]"
                for fid, path, name, src, cloud in self.profiles
            ]
            idx = next((i for i, p in enumerate(self.profiles) if p[1] == prev_path), 0)
            self.cb_profile.configure(values=display_values)
            self.cb_profile.set(display_values[idx])
            self.selected_profile_index = idx
        else:
            self.cb_profile.configure(values=[self.t('path_status_not_found')])
            self.cb_profile.set(self.t('path_status_not_found'))
            self.selected_profile_index = -1

    def on_profile_selected(self, choice: str):
        values = self.cb_profile._values
        if choice in values:
            self.selected_profile_index = values.index(choice)
            self.refresh_saves_list()
            self.refresh_config_tweaks()
            self.refresh_profile_card()
            self.refresh_save_slots()
            if getattr(self, 'installed_mods', None) is not None:
                self.render_mods_view()

    def get_selected_profile_path(self) -> Path | None:
        if 0 <= self.selected_profile_index < len(self.profiles):
            return self.profiles[self.selected_profile_index][1]
        return None

    def profile_item_path(self, item: str, profile: tuple | None = None) -> Path | None:
        """Реальне розташування файлу/папки профілю (за замовчуванням — вибраного) з урахуванням Steam Cloud."""
        if profile is None:
            if not (0 <= self.selected_profile_index < len(self.profiles)):
                return None
            profile = self.profiles[self.selected_profile_index]
        _, local_dir, _, _, cloud_dir = profile
        local = local_dir / item
        if cloud_dir and item in CLOUD_ITEMS:
            remote = cloud_dir / item
            if remote.exists() or not local.exists():
                return remote
        return local

    # --- КАРТКА ПРОФІЛЮ ТА СЛОТИ СЕЙВІВ ---
    def get_save_slots(self) -> list:
        """Слоти сейвів вибраного профілю, найсвіжіші першими."""
        save_dir = self.profile_item_path('save')
        slots = []
        if save_dir and save_dir.is_dir():
            for d in save_dir.iterdir():
                if not (d.is_dir() and (d / 'game.sii').exists()):
                    continue
                info = read_sii_file(d / 'info.sii') or {}
                try:
                    file_time = int(info.get('file_time') or 0) or int(d.stat().st_mtime)
                except (ValueError, OSError):
                    file_time = 0
                slots.append({'dir': d, 'info': info, 'file_time': file_time})
        slots.sort(key=lambda s: s['file_time'], reverse=True)
        return slots

    @staticmethod
    def _fmt_int(val) -> str:
        try:
            return f"{int(float(val)):,}".replace(",", " ")
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _fmt_ts(val, with_time: bool = True) -> str:
        try:
            return datetime.fromtimestamp(int(val)).strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")
        except (TypeError, ValueError, OSError):
            return "—"

    def refresh_profile_card(self):
        vals = {key: "—" for key, _ in self.PROFILE_STATS}
        head, sub = self.t('profile_none'), ""

        if self.get_selected_profile_path() is not None:
            _, _, display_name, _, cloud = self.profiles[self.selected_profile_index]
            p = self.profile_item_path('profile.sii')
            prof = read_sii_file(p) if p else None
            slots = self.get_save_slots()
            latest = slots[0]['info'] if slots else {}
            ptype = self.t('profile_type_cloud') if cloud else self.t('profile_type_local')

            if prof is None:
                head = f"👤 {display_name}"
                sub = f"{ptype} · {self.t('profile_unreadable')}"
            else:
                company = prof.get('company_name') or ""
                head = f"👤 {prof.get('profile_name') or display_name}" + (f"  ·  🏢 {company}" if company else "")
                brand = (prof.get('brand') or "—").replace('_', ' ').upper()
                sub = self.t('profile_sub').format(ptype, brand, self._fmt_ts(prof.get('creation_time'), False))
                # Гра зберігає пробіг у кілометрах для обох ігор
                unit = "km"
                vals["experience"] = self._fmt_int(prof.get('cached_experience'))
                vals["distance"] = f"{self._fmt_int(prof.get('cached_distance'))} {unit}"
                vals["mods"] = str(len(prof.get('active_mods') or []))
                vals["last_save"] = self._fmt_ts(prof.get('save_time'))

            currency = "$" if self.current_game == "American Truck Simulator" else "€"
            if latest.get('info_money_account') is not None:
                vals["money"] = f"{self._fmt_int(latest.get('info_money_account'))} {currency}"
            if latest.get('info_visited_cities') is not None:
                vals["cities"] = self._fmt_int(latest.get('info_visited_cities'))
            explored = sii_float(latest.get('info_explored_ratio'))
            if explored is not None:
                vals["explored"] = f"{explored * 100:.1f}%"
            vals["slots"] = str(len(slots))

        self.lbl_profile_head.configure(text=head)
        self.lbl_profile_sub.configure(text=sub)
        for key, (val_lbl, cap_lbl, caption_key) in self.profile_stat_labels.items():
            val_lbl.configure(text=vals[key])
            cap_lbl.configure(text=self.t(caption_key))

    # --- СЛОТИ СЕЙВІВ ---
    SLOT_MARKER = "tcm_slot.json"

    def _slot_title(self, slot: dict) -> str:
        name = (slot['info'].get('name') or "").strip()
        if name:
            return name
        folder = slot['dir'].name
        for prefix, key in (("autosave_drive", 'slot_autosave_drive'), ("autosave_job", 'slot_autosave_job'),
                            ("multiplayer_backup", 'slot_mp_backup'), ("autosave", 'slot_autosave'),
                            ("quicksave", 'slot_quicksave')):
            if folder.startswith(prefix):
                return self.t(key)
        return folder

    def _slot_missing_mods(self, slot: dict) -> list:
        mods = getattr(self, 'installed_mods', None)
        if mods is None:
            return []
        missing = []
        for dep in slot['info'].get('dependencies', []) or []:
            kind, _, rest = dep.partition('|')
            if kind != 'mod':
                continue
            pkg, _, name = rest.partition('|')
            if pkg not in mods:
                missing.append(name or pkg)
        return missing

    def refresh_save_slots(self):
        for w in self.slots_frame.winfo_children():
            w.destroy()
        if self.get_selected_profile_path() is None:
            ctk.CTkLabel(self.slots_frame, text=self.t('profile_none'), text_color=COLOR_TEXT_MUTED).pack(pady=20)
            return
        slots = self.get_save_slots()
        if not slots:
            ctk.CTkLabel(self.slots_frame, text=self.t('slots_empty'), text_color=COLOR_TEXT_MUTED).pack(pady=20)
            return
        currency = "$" if self.current_game == "American Truck Simulator" else "€"
        for slot in slots:
            self._create_slot_row(slot, currency)

    def _create_slot_row(self, slot: dict, currency: str):
        info = slot['info']
        missing = self._slot_missing_mods(slot)
        card = ctk.CTkFrame(self.slots_frame, corner_radius=8, fg_color="#202227",
                            border_width=1 if missing else 0, border_color=COLOR_DANGER)
        card.pack(fill="x", pady=3, padx=4)
        card.grid_columnconfigure(0, weight=1)

        title = f"{'⚠ ' if missing else '💾 '}{self._slot_title(slot)}"
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                     text_color="#FCA5A5" if missing else COLOR_TEXT_WHITE
                     ).grid(row=0, column=0, padx=12, pady=(6, 0), sticky="w")

        parts = [self._fmt_ts(slot['file_time']), slot['dir'].name]
        if info.get('info_money_account') is not None:
            parts.append(f"{self._fmt_int(info.get('info_money_account'))} {currency}")
        if info.get('info_players_experience') is not None:
            parts.append(f"XP {self._fmt_int(info.get('info_players_experience'))}")
        n_mods = sum(1 for d in info.get('dependencies', []) or [] if d.startswith('mod|'))
        parts.append(self.t('slot_mods').format(n_mods))
        ctk.CTkLabel(card, text="  ·  ".join(parts), font=ctk.CTkFont(family="Segoe UI", size=11),
                     text_color=COLOR_TEXT_MUTED).grid(row=1, column=0, padx=12, sticky="w")
        if missing:
            shown = ", ".join(missing[:3]) + (f" (+{len(missing) - 3})" if len(missing) > 3 else "")
            ctk.CTkLabel(card, text=self.t('slot_missing').format(len(missing), shown),
                         font=ctk.CTkFont(family="Segoe UI", size=11), text_color="#F87171", justify="left"
                         ).grid(row=2, column=0, padx=12, sticky="w")

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.grid(row=0, column=1, rowspan=3, padx=10, pady=6, sticky="e")
        ctk.CTkButton(btn_box, text=self.t('btn_slot_edit'), width=110, height=28,
                      fg_color=UA_BLUE, hover_color=UA_BLUE_HOVER,
                      command=lambda s=slot: self.edit_save_slot(s)).pack(side="left", padx=2)
        ctk.CTkButton(btn_box, text=self.t('btn_slot_backup'), width=90, height=28,
                      fg_color="#374151", hover_color="#4B5563",
                      command=lambda s=slot: self.backup_save_slot(s)).pack(side="left", padx=2)
        b_open = ctk.CTkButton(btn_box, text="↗", width=32, height=28, fg_color="#374151", hover_color="#4B5563",
                               command=lambda d=slot['dir']: os.startfile(d))
        b_open.pack(side="left", padx=2)
        Tooltip(b_open, self.t('btn_open_game_folder'))
        ctk.CTkLabel(card, text="", height=4).grid(row=3, column=0)

    def edit_save_slot(self, slot: dict):
        game_file = slot['dir'] / 'game.sii'
        try:
            data = load_save_data(game_file)
            fields = read_save_fields(data)
        except Exception as e:
            self.logger.error(f"Save editor: cannot read {game_file}: {e}")
            messagebox.showerror(self.t('dlg_error'), self.t('editor_cannot_read').format(e))
            return

        currency = "$" if self.current_game == "American Truck Simulator" else "€"
        values = {k: v[0] for k, v in fields.items()}
        dlg = SaveEditorDialog(self, self._slot_title(slot), values, currency, self.t)
        self.wait_window(dlg)
        changes = {k: v for k, v in (dlg.result or {}).items() if v != values[k]}
        if not changes:
            return

        if is_game_running(self.current_game):
            messagebox.showerror(self.t('dlg_error'), self.t('editor_game_running'))
            return
        confirm = self.t('editor_confirm').format(self._slot_title(slot), "\n".join(
            f"  {self.t('editor_field_' + k)}: {values[k]} → {v}" for k, v in changes.items()))
        if self._is_in_steam_cloud(slot['dir']):
            confirm += self.t('cloud_restore_note')
        if not messagebox.askyesno(self.t('dlg_save_editor'), confirm):
            return

        try:
            new_data = patch_save_fields(data, changes)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return
        backup = self.backup_save_slot(slot, prefix="_pre_edit_")
        if backup is None:
            return
        try:
            tmp = game_file.with_name('game.sii.__tcm_tmp')
            tmp.write_bytes(new_data)
            os.replace(tmp, game_file)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        self.logger.info(f"Save edited: {game_file} {changes} (backup {backup.name})")
        self.refresh_saves_list()
        self.refresh_save_slots()
        self.refresh_profile_card()
        self.set_status(self.t('editor_done_status').format(self._slot_title(slot)))
        messagebox.showinfo("OK", self.t('editor_done').format(backup.name))

    def _zip_folder(self, src: Path, arc_path: Path, marker: dict | None = None):
        with zipfile.ZipFile(arc_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            if marker is not None:
                zf.writestr(self.SLOT_MARKER, json.dumps(marker, ensure_ascii=False))
            for f in src.rglob('*'):
                if f.is_file():
                    zf.write(f, f.relative_to(src))

    def backup_save_slot(self, slot: dict, prefix: str = "") -> Path | None:
        prof_name = self.profiles[self.selected_profile_index][2]
        safe_prof = "".join(c for c in prof_name if c.isalnum() or c in " _-()").strip() or "profile"
        arc = self.get_saves_backup_path(create=True) / \
            f"{prefix}{safe_prof}_slot_{slot['dir'].name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        try:
            self._zip_folder(slot['dir'], arc, {"slot": slot['dir'].name, "title": self._slot_title(slot),
                                                "profile": prof_name, "game": self.current_game})
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return None
        self.logger.info(f"Slot backup created: {arc}")
        if not prefix:
            self.refresh_saves_list()
            self.set_status(self.t('slot_backup_done').format(self._slot_title(slot)))
            messagebox.showinfo("OK", self.t('save_zip_created').format(arc.name, get_dir_size_str(arc)))
        return arc

    @classmethod
    def read_slot_marker(cls, arc_path: Path) -> dict | None:
        try:
            with zipfile.ZipFile(arc_path) as zf:
                if cls.SLOT_MARKER in zf.namelist():
                    return json.loads(zf.read(cls.SLOT_MARKER).decode('utf-8'))
        except Exception:
            pass
        return None

    # --- ПРЕСЕТИ КЕРУВАННЯ ---
    def refresh_presets_list(self):
        for w in self.presets_frame.winfo_children():
            w.destroy()

        p_dir = self.get_presets_path()
        self.presets = []

        if p_dir.exists():
            for item in sorted(p_dir.iterdir()):
                if item.is_dir() and not item.name.startswith('_'):
                    self.presets.append(item)

        if not self.presets:
            ctk.CTkLabel(
                self.presets_frame,
                text=self.t('presets_empty'),
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#9CA3AF"
            ).pack(pady=24)
            self.selected_preset = None
            return

        for p in self.presets:
            self._create_preset_row(p)

    def _create_preset_row(self, preset_path: Path):
        is_selected = (self.selected_preset == preset_path)
        card = ctk.CTkFrame(
            self.presets_frame,
            corner_radius=8,
            fg_color="#2D3039" if is_selected else "#202227",
            border_width=2 if is_selected else 0,
            border_color=UA_YELLOW
        )
        card.pack(fill="x", pady=4, padx=4)
        card.grid_columnconfigure(0, weight=1)

        info_box = ctk.CTkFrame(card, fg_color="transparent")
        info_box.grid(row=0, column=0, padx=12, pady=10, sticky="w")

        lbl_name = ctk.CTkLabel(
            info_box,
            text=f"🎮 {preset_path.name}",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=UA_YELLOW if is_selected else COLOR_TEXT_WHITE
        )
        lbl_name.pack(anchor="w")

        files_count = len([f for f in preset_path.iterdir() if f.is_file()])
        mtime = datetime.fromtimestamp(preset_path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        ctk.CTkLabel(
            info_box,
            text=self.t('preset_row_info').format(files_count, mtime),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        ).pack(anchor="w")

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.grid(row=0, column=1, padx=10, pady=10, sticky="e")

        ctk.CTkButton(
            btn_box,
            text=self.t('btn_pick') if not is_selected else self.t('btn_picked'),
            width=80,
            height=28,
            fg_color=UA_BLUE if is_selected else "#374151",
            hover_color=UA_BLUE_HOVER,
            command=lambda p=preset_path: self.select_preset(p)
        ).pack(side="left", padx=4)

        b_exp = ctk.CTkButton(
            btn_box,
            text="📤",
            width=36,
            height=28,
            fg_color="#374151",
            hover_color="#4B5563",
            command=lambda p=preset_path: self.export_preset_action(p)
        )
        b_exp.pack(side="left", padx=2)
        Tooltip(b_exp, self.t('export_preset_title'))

        b_del = ctk.CTkButton(
            btn_box,
            text="🗑",
            width=36,
            height=28,
            fg_color=COLOR_DANGER,
            hover_color=COLOR_DANGER_HOVER,
            command=lambda p=preset_path: self.delete_preset_action(p)
        )
        b_del.pack(side="left", padx=2)
        Tooltip(b_del, self.t('btn_delete'))

    def select_preset(self, preset_path: Path):
        self.selected_preset = preset_path
        self.refresh_presets_list()

    def _collect_profile_files(self, profile_path: Path) -> list:
        game_path = self.get_active_game_path()
        collected = []
        for f in PROFILE_FILES:
            src = self.profile_item_path(f) or profile_path / f
            if src.exists():
                collected.append((src, f))
        for f in GLOBAL_FILES:
            src = game_path / f
            if src.exists():
                collected.append((src, f))
        return collected

    def save_new_preset(self):
        pp = self.get_selected_profile_path()
        if not pp:
            messagebox.showerror(self.t('dlg_error'), self.t('pick_profile'))
            return

        name = simpledialog.askstring(self.t('preset_name_title'), self.t('preset_name_prompt'))
        if not name:
            return

        safe_name = "".join(c for c in name if c.isalnum() or c in " _-()").strip()
        if not safe_name:
            messagebox.showerror(self.t('dlg_error'), self.t('bad_preset_name'))
            return

        # Спершу збираємо файли, щоб не знести наявний пресет, якщо копіювати нічого
        files = self._collect_profile_files(pp)
        if not files:
            messagebox.showerror(self.t('dlg_error'), self.t('no_control_files'))
            return

        pd = self.get_presets_path() / safe_name
        if pd.exists():
            if not messagebox.askyesno(self.t('dlg_confirm'), self.t('preset_exists').format(safe_name)):
                return

        try:
            if pd.exists():
                shutil.rmtree(pd)
            pd.mkdir(parents=True, exist_ok=True)
            for src, rel in files:
                shutil.copy2(src, pd / rel)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        self.logger.info(f"Preset saved: {safe_name}")
        self.set_status(self.t('preset_saved').format(safe_name))
        self.selected_preset = pd
        self.refresh_presets_list()
        messagebox.showinfo("OK", self.t('preset_saved').format(safe_name))

    def apply_selected_preset(self):
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        if not self.selected_preset or not self.selected_preset.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('pick_preset'))
            return

        pp = self.get_selected_profile_path()
        if not pp:
            messagebox.showerror(self.t('dlg_error'), self.t('pick_profile'))
            return

        profile_display = self.profiles[self.selected_profile_index][2]
        if not messagebox.askyesno(self.t('dlg_confirm'), self.t('preset_apply_confirm').format(self.selected_preset.name, profile_display)):
            return

        gp = self.get_active_game_path()
        bdir = self.get_presets_path() / f"_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        bdir.mkdir(parents=True, exist_ok=True)

        try:
            applied = []
            for n in PROFILE_FILES:
                src = self.selected_preset / n
                dst = self.profile_item_path(n) or pp / n
                if src.exists():
                    if dst.exists():
                        shutil.copy2(dst, bdir / n)
                    shutil.copy2(src, dst)
                    applied.append(n)

            for n in GLOBAL_FILES:
                src = self.selected_preset / n
                dst = gp / n
                if src.exists():
                    if dst.exists():
                        shutil.copy2(dst, bdir / n)
                    shutil.copy2(src, dst)
                    applied.append(n)

            self.logger.info(f"Applied preset {self.selected_preset.name} to {pp}")
            self.set_status(self.t('preset_applied').format(self.selected_preset.name))
            messagebox.showinfo("OK", self.t('preset_applied').format(self.selected_preset.name))
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))

    def create_quick_backup(self):
        pp = self.get_selected_profile_path()
        if not pp:
            messagebox.showerror(self.t('dlg_error'), self.t('pick_profile'))
            return

        bdir = self.get_presets_path() / f"_manual_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        files = self._collect_profile_files(pp)
        if not files:
            messagebox.showwarning("!", self.t('no_control_files'))
            return

        bdir.mkdir(parents=True, exist_ok=True)
        for src, rel in files:
            shutil.copy2(src, bdir / rel)

        self.logger.info(f"Manual backup created: {bdir}")
        self.set_status(self.t('msg_backup_created').format(bdir.name))
        messagebox.showinfo("OK", self.t('manual_backup_ok').format(bdir))

    def delete_preset_action(self, preset_path: Path):
        if messagebox.askyesno(self.t('dlg_delete'), self.t('preset_delete_confirm').format(preset_path.name)):
            try:
                shutil.rmtree(preset_path)
                if self.selected_preset == preset_path:
                    self.selected_preset = None
                self.set_status(self.t('preset_deleted').format(preset_path.name))
                self.refresh_presets_list()
            except Exception as e:
                messagebox.showerror(self.t('dlg_error'), str(e))

    # --- ЕКСПОРТ / ІМПОРТ ПРЕСЕТІВ ---
    def export_preset_action(self, preset_path: Path):
        target = filedialog.asksaveasfilename(
            title=self.t('export_preset_title'),
            initialdir=str(get_documents_dir()),
            initialfile=f"{preset_path.name}.zip",
            defaultextension=".zip",
            filetypes=[("ZIP", "*.zip")]
        )
        if not target:
            return

        allowed = PROFILE_FILES + GLOBAL_FILES
        files = [f for f in preset_path.iterdir() if f.is_file() and f.name in allowed]
        manifest = {
            "app": APP_NAME,
            "app_version": APP_VERSION,
            "game": self.current_game,
            "name": preset_path.name,
            "created": datetime.now().isoformat(timespec='seconds'),
            "files": [f.name for f in files],
        }
        try:
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(PRESET_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
                for f in files:
                    zf.write(f, f.name)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        self.logger.info(f"Preset exported: {preset_path.name} -> {target}")
        self.set_status(self.t('preset_exported').format(preset_path.name, target).splitlines()[0])
        messagebox.showinfo("OK", self.t('preset_exported').format(preset_path.name, target))

    def import_preset_action(self):
        src = filedialog.askopenfilename(
            title=self.t('import_preset_title'),
            initialdir=str(get_documents_dir()),
            filetypes=[("ZIP", "*.zip")]
        )
        if not src:
            return

        allowed = PROFILE_FILES + GLOBAL_FILES
        try:
            with zipfile.ZipFile(src, 'r') as zf:
                manifest = {}
                if PRESET_MANIFEST in zf.namelist():
                    try:
                        manifest = json.loads(zf.read(PRESET_MANIFEST).decode('utf-8'))
                    except Exception:
                        manifest = {}

                # Беремо лише відомі файли за їхнім іменем (і з архіву папки теж),
                # нічого не розпаковуємо за шляхами з архіву
                picked = {}
                for info in zf.infolist():
                    base = info.filename.replace('\\', '/').rsplit('/', 1)[-1]
                    if info.is_dir() or base not in allowed or base in picked:
                        continue
                    if info.file_size > PRESET_MAX_FILE_SIZE:
                        messagebox.showerror(self.t('dlg_error'), self.t('preset_import_too_big').format(base))
                        return
                    picked[base] = zf.read(info)
        except zipfile.BadZipFile as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        if "controls.sii" not in picked:
            messagebox.showerror(self.t('dlg_error'), self.t('preset_import_bad').format(", ".join(allowed)))
            return

        game = manifest.get("game")
        if game in GAMES and game != self.current_game:
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('preset_import_other_game').format(game, self.current_game)):
                return

        raw_name = str(manifest.get("name") or Path(src).stem)
        safe_name = "".join(c for c in raw_name if c.isalnum() or c in " _-()").strip().lstrip('_') or "Imported"
        pd = self.get_presets_path(create=True) / safe_name
        if pd.exists() and not messagebox.askyesno(self.t('dlg_confirm'), self.t('preset_exists').format(safe_name)):
            return

        try:
            if pd.exists():
                shutil.rmtree(pd)
            pd.mkdir(parents=True)
            for name, data in picked.items():
                (pd / name).write_bytes(data)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        self.logger.info(f"Preset imported: {src} -> {pd}")
        self.selected_preset = pd
        self.refresh_presets_list()
        self.set_status(self.t('preset_imported').format(safe_name))
        messagebox.showinfo("OK", self.t('preset_imported').format(safe_name))

    # --- ТВІКИ ТА КОНСОЛЬ ---
    def _get_config_str(self, text: str, key: str, default: str = "") -> str:
        m = re.search(rf'uset\s+{re.escape(key)}\s+"([^"]*)"', text, re.I)
        return m.group(1).strip() if m else default

    def _get_config_float(self, text: str, key: str, default: float = 1.0) -> float:
        val_str = self._get_config_str(text, key, "")
        try:
            return float(val_str)
        except Exception:
            return default

    # Профільні змінні (поліція, втома, фізика, гальма...) гра читає з файлів профілю:
    # config.cfg (у Steam Cloud профілів — у Steam\userdata\...\remote) та config_local.cfg.
    def _read_profile_cfgs(self) -> dict:
        texts = {}
        for name in ("config.cfg", "config_local.cfg"):
            p = self.profile_item_path(name)
            if p and p.exists():
                try:
                    texts[p] = p.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    pass
        return texts

    def _locate_profile_key(self, key: str, texts: dict) -> Path | None:
        """Файл профілю, де вже є змінна; інакше — файл за замовчуванням для неї."""
        for p, text in texts.items():
            if re.search(rf'^\s*uset\s+{re.escape(key)}\s', text, re.I | re.M):
                return p
        return self.profile_item_path(PROFILE_CVARS[key])

    def _get_profile_str(self, texts: dict, key: str, default: str) -> str:
        p = self._locate_profile_key(key, texts)
        return self._get_config_str(texts.get(p, ""), key, default)

    def _get_profile_float(self, texts: dict, key: str, default: float) -> float:
        try:
            return float(self._get_profile_str(texts, key, ""))
        except ValueError:
            return default

    @staticmethod
    def _set_switch(switch, on: bool):
        if on:
            switch.select()
        else:
            switch.deselect()

    @staticmethod
    def _set_slider(slider, label, value: float, lo: float, hi: float, fmt: str):
        value = min(max(value, lo), hi)
        slider.set(value)
        label.configure(text=format(value, fmt))

    def refresh_config_tweaks(self):
        cfg = self.get_active_game_path() / 'config.cfg'
        try:
            # 1. Загальний config.cfg гри
            if cfg.exists():
                text = cfg.read_text(encoding='utf-8', errors='ignore')
                dev = self._get_config_str(text, "g_developer", "0") == "1"
                con = self._get_config_str(text, "g_console", "0") == "1"
                if dev and con:
                    self.lbl_con_status.configure(text=self.t('console_status_on'), text_color="#10B981")
                else:
                    self.lbl_con_status.configure(text=self.t('console_status_off'), text_color="#9CA3AF")
            else:
                text = ""
                self.lbl_con_status.configure(text=self.t('msg_cfg_missing_short'), text_color="#EF4444")

            self._set_switch(self.sw_minicon, self._get_config_str(text, "g_minicon", "0") == "1")
            self._set_slider(self.slider_traffic, self.lbl_traffic_val,
                             self._get_config_float(text, "g_traffic", 1.0), 0.0, 10.0, ".1f")
            self._set_slider(self.slider_flyspeed, self.lbl_flyspeed_val,
                             self._get_config_float(text, "g_flyspeed", 100.0), 10.0, 300.0, ".0f")

            # Ліміт FPS — t_limit_fps (g_fps лише показує FPS у міні-консолі).
            # Порівнюємо як числа: "60.0" має збігтися з "60", а "1" не повинно збігтися з "120"
            fps_num = self._get_config_float(text, "t_limit_fps", 0.0)
            match = [v for v in self.cb_fps._values if float(v.split()[0]) == fps_num]
            if match:
                self.cb_fps.set(match[0])
            elif fps_num > 0:
                # Нестандартний ліміт (наприклад 100) показуємо як є, щоб не затерти його при збереженні
                self.cb_fps.set(f"{fps_num:g}")
            else:
                self.cb_fps.set(self.cb_fps._values[0])

            self._set_switch(self.sw_pedestrian, self._get_config_str(text, "g_pedestrian", "1") != "0")
            self._set_switch(self.sw_cloud_shadows, self._get_config_str(text, "r_cloud_shadows", "1") != "0")

            # 2. Файли вибраного профілю
            has_profile = self.get_selected_profile_path() is not None
            if has_profile:
                suffix = f"  ·  👤 {self.profiles[self.selected_profile_index][2]}"
            else:
                suffix = f"  ·  {self.t('tweak_no_profile')}"
            self.lbl_card_rules.configure(text=f"⚖️ {self.t('tweak_card_rules')}{suffix}")
            self.lbl_card_physics.configure(text=f"🚚 {self.t('tweak_card_physics')}{suffix}")

            ptexts = self._read_profile_cfgs() if has_profile else {}

            self._set_switch(self.sw_police, self._get_profile_str(ptexts, "g_police", "1") != "0")
            self._set_switch(self.sw_fatigue, self._get_profile_str(ptexts, "g_fatigue", "1") != "0")
            self._set_switch(self.sw_steering_wheel, self._get_profile_str(ptexts, "r_steering_wheel", "1") != "0")

            self._set_slider(self.slider_cam_physics, self.lbl_cam_physics_val,
                             self._get_profile_float(ptexts, "g_cam_physics_value", 1.0), 0.0, 2.0, ".1f")
            self._set_slider(self.slider_cabin_stiffness, self.lbl_cabin_stiffness_val,
                             self._get_profile_float(ptexts, "g_cabin_suspension_stiffness", 1.0), 0.0, 1.0, ".2f")
            self._set_slider(self.slider_truck_stability, self.lbl_truck_stability_val,
                             self._get_profile_float(ptexts, "g_truck_stability", 0.5), 0.0, 1.0, ".2f")
            self._set_slider(self.slider_trailer_stability, self.lbl_trailer_stability_val,
                             self._get_profile_float(ptexts, "g_trailer_stability", 0.5), 0.0, 1.0, ".2f")
            self._set_slider(self.slider_brake_intensity, self.lbl_brake_intensity_val,
                             self._get_profile_float(ptexts, "g_brake_intensity", 1.0), 0.1, 2.0, ".2f")

            # Стан скидання ринку вантажів
            self.economy_reset_pending = self._get_profile_str(ptexts, "g_force_economy_reset", "0") == "1"
            if self.economy_reset_pending:
                self.btn_reset_economy.configure(text=self.t('btn_reset_economy_undo'))
                self.lbl_reset_economy_hint.configure(text=self.t('economy_reset_pending'), text_color=UA_YELLOW)
            else:
                self.btn_reset_economy.configure(text=self.t('btn_reset_economy'))
                self.lbl_reset_economy_hint.configure(text=self.t('btn_reset_economy_hint'), text_color=COLOR_TEXT_MUTED)

        except Exception as e:
            self.logger.error(f"Error reading tweaks: {e}")

    def _backup_config(self, cfg: Path, keep: int = 5):
        """Бекап файлу конфігу перед записом; зберігаються лише `keep` останніх копій.
        Файли зі Steam Cloud бекапляться в папку гри, щоб Steam не вивантажував копії в хмару."""
        if self._is_in_steam_cloud(cfg):
            bdir = self.get_active_game_path() / CONFIG_BACKUP_DIR_NAME / cfg.parent.name
            bdir.mkdir(parents=True, exist_ok=True)
        else:
            bdir = cfg.parent
        shutil.copy2(cfg, bdir / f"{cfg.name}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        baks = sorted(bdir.glob(f"{cfg.name}.bak_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in baks[keep:]:
            try:
                old.unlink()
            except Exception:
                pass

    @staticmethod
    def _is_in_steam_cloud(path: Path) -> bool:
        parts = [p.lower() for p in path.parts]
        return 'userdata' in parts and 'remote' in parts

    def _patch_config_line(self, text: str, key: str, value: str) -> str:
        pattern = re.compile(rf'(uset\s+{re.escape(key)}\s+)"[^"]*"', re.I)
        repl = f'\\1"{value}"'
        if pattern.search(text):
            return pattern.sub(repl, text)
        return text.rstrip() + f'\nuset {key} "{value}"\n'

    def _write_cfg_patches(self, patches_by_file: dict):
        """Застосовує {файл: {змінна: значення}}; перед записом кожного файлу робить бекап."""
        for path, patches in patches_by_file.items():
            if path.exists():
                raw = path.read_text(encoding='utf-8', errors='ignore')
            elif path.parent.exists():
                raw = ""
            else:
                raise FileNotFoundError(self.t('msg_dir_not_found').format(path.parent))
            for key, value in patches.items():
                raw = self._patch_config_line(raw, key, value)
            if path.exists():
                self._backup_config(path)
            path.write_text(raw, encoding='utf-8')
            self.logger.info(f"Config updated: {path} -> {patches}")

    def _profile_patches(self, values: dict) -> dict:
        """Розкладає профільні змінні по файлах профілю, де вони мають лежати."""
        texts = self._read_profile_cfgs()
        result = {}
        for key, value in values.items():
            target = self._locate_profile_key(key, texts)
            if target:
                result.setdefault(target, {})[key] = value
        return result

    def save_gameplay_tweaks(self):
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        cfg = self.get_active_game_path() / 'config.cfg'
        if not cfg.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('msg_cfg_missing').format(cfg))
            return

        global_values = {
            "g_minicon": "1" if self.sw_minicon.get() else "0",
            "g_traffic": f"{self.slider_traffic.get():.1f}",
            "g_flyspeed": f"{int(self.slider_flyspeed.get())}.0",
            "t_limit_fps": self.cb_fps.get().split()[0],
            "g_pedestrian": "1" if self.sw_pedestrian.get() else "0",
            "r_cloud_shadows": "1" if self.sw_cloud_shadows.get() else "0",
        }
        profile_values = {
            "g_police": "1" if self.sw_police.get() else "0",
            "g_fatigue": "1" if self.sw_fatigue.get() else "0",
            "r_steering_wheel": "1" if self.sw_steering_wheel.get() else "0",
            "g_cam_physics": "1",
            "g_cam_physics_value": f"{self.slider_cam_physics.get():.1f}",
            "g_cabin_suspension_stiffness": f"{self.slider_cabin_stiffness.get():.2f}",
            "g_truck_stability": f"{self.slider_truck_stability.get():.2f}",
            "g_trailer_stability": f"{self.slider_trailer_stability.get():.2f}",
            "g_brake_intensity": f"{self.slider_brake_intensity.get():.2f}",
        }

        has_profile = self.get_selected_profile_path() is not None
        try:
            patches = {cfg: global_values}
            if has_profile:
                for path, values in self._profile_patches(profile_values).items():
                    patches.setdefault(path, {}).update(values)
            self._write_cfg_patches(patches)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        self.set_status(self.t('tweaks_saved'))
        if has_profile:
            messagebox.showinfo("OK", self.t('tweaks_saved'))
        else:
            messagebox.showwarning("!", self.t('tweaks_saved') + "\n\n" + self.t('tweaks_profile_skipped'))

    def reset_freight_economy(self):
        if not self.get_selected_profile_path():
            messagebox.showerror(self.t('dlg_error'), self.t('pick_profile'))
            return
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        # Повторне натискання повертає змінну в 0 — гра не завжди скидає її сама
        enable = not getattr(self, 'economy_reset_pending', False)
        try:
            self._write_cfg_patches(self._profile_patches({"g_force_economy_reset": "1" if enable else "0"}))
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return

        self.refresh_config_tweaks()
        msg = self.t('economy_reset_done') if enable else self.t('economy_reset_undone')
        self.set_status(msg.splitlines()[0])
        messagebox.showinfo("OK", msg)

    def restore_default_tweaks(self):
        if not messagebox.askyesno(self.t('dlg_confirm'), self.t('restore_defaults_confirm')):
            return

        self.slider_traffic.set(1.0)
        self.lbl_traffic_val.configure(text="1.0")

        self.slider_flyspeed.set(100.0)
        self.lbl_flyspeed_val.configure(text="100")

        self.cb_fps.set(self.cb_fps._values[0])

        self.sw_minicon.deselect()
        self.sw_police.select()
        self.sw_fatigue.select()
        self.sw_steering_wheel.select()

        self.slider_cam_physics.set(1.0)
        self.lbl_cam_physics_val.configure(text="1.0")

        self.slider_cabin_stiffness.set(1.0)
        self.lbl_cabin_stiffness_val.configure(text="1.00")

        self.slider_truck_stability.set(0.5)
        self.lbl_truck_stability_val.configure(text="0.50")

        self.slider_trailer_stability.set(0.5)
        self.lbl_trailer_stability_val.configure(text="0.50")

        self.slider_brake_intensity.set(1.0)
        self.lbl_brake_intensity_val.configure(text="1.00")

        self.sw_pedestrian.select()
        self.sw_cloud_shadows.select()

        self.save_gameplay_tweaks()

    def set_console_state(self, enable: bool):
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        cfg = self.get_active_game_path() / 'config.cfg'
        if not cfg.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('msg_cfg_missing').format(cfg))
            return

        try:
            raw = cfg.read_text(encoding='utf-8', errors='ignore')
            val = "1" if enable else "0"
            raw = self._patch_config_line(raw, "g_developer", val)
            raw = self._patch_config_line(raw, "g_console", val)

            self._backup_config(cfg)
            cfg.write_text(raw, encoding='utf-8')

            msg =self.t('console_done_on') if enable else self.t('console_done_off')
            self.refresh_config_tweaks()
            self.set_status(msg)
            messagebox.showinfo("OK", msg)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))

    # --- БЕКАПИ СЕЙВІВ ---
    def refresh_saves_list(self):
        for w in self.saves_frame.winfo_children():
            w.destroy()

        b_dir = self.get_saves_backup_path()
        self.save_archives = []

        if b_dir.exists():
            for f in sorted(b_dir.glob("*.zip"), reverse=True):
                self.save_archives.append(f)

        if not self.save_archives:
            ctk.CTkLabel(
                self.saves_frame,
                text=self.t('saves_empty'),
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#9CA3AF"
            ).pack(pady=20)
            self.selected_save_archive = None
            return

        for arc in self.save_archives:
            self._create_save_archive_row(arc)

    def _create_save_archive_row(self, arc_path: Path):
        is_selected = (self.selected_save_archive == arc_path)
        card = ctk.CTkFrame(
            self.saves_frame,
            corner_radius=8,
            fg_color="#2D3039" if is_selected else "#202227",
            border_width=2 if is_selected else 0,
            border_color=UA_YELLOW
        )
        card.pack(fill="x", pady=4, padx=4)
        card.grid_columnconfigure(0, weight=1)

        info_box = ctk.CTkFrame(card, fg_color="transparent")
        info_box.grid(row=0, column=0, padx=12, pady=8, sticky="w")

        ctk.CTkLabel(
            info_box,
            text=f"📦 {arc_path.name}",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=UA_YELLOW if is_selected else COLOR_TEXT_WHITE
        ).pack(anchor="w")

        size_kb = arc_path.stat().st_size / 1024
        size_str = f"{size_kb / 1024:.2f} MB" if size_kb > 1024 else f"{size_kb:.1f} KB"
        mtime = datetime.fromtimestamp(arc_path.stat().st_mtime).strftime("%d.%m.%Y %H:%M:%S")
        marker = self.read_slot_marker(arc_path)
        kind = self.t('archive_kind_slot').format(marker.get('title') or marker.get('slot')) if marker \
            else self.t('archive_kind_full')

        ctk.CTkLabel(
            info_box,
            text=self.t('archive_row_info').format(size_str, mtime, kind),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLOR_TEXT_MUTED
        ).pack(anchor="w")

        btn = ctk.CTkButton(
            card,
            text=self.t('btn_pick') if not is_selected else self.t('btn_picked'),
            width=80,
            height=28,
            fg_color=UA_BLUE if is_selected else "#374151",
            hover_color=UA_BLUE_HOVER,
            command=lambda p=arc_path: self.select_save_archive(p)
        )
        btn.grid(row=0, column=1, padx=12, pady=8, sticky="e")

    def select_save_archive(self, arc_path: Path):
        self.selected_save_archive = arc_path
        self.refresh_saves_list()

    def create_save_zip_backup(self):
        pp = self.get_selected_profile_path()
        if not pp:
            messagebox.showerror(self.t('dlg_error'), self.t('pick_profile'))
            return

        save_dir = self.profile_item_path('save')
        if not save_dir.exists() or not any(save_dir.iterdir()):
            messagebox.showerror(self.t('dlg_error'), self.t('no_save_folder'))
            return

        prof_name = self.profiles[self.selected_profile_index][2]
        safe_name = "".join(c for c in prof_name if c.isalnum() or c in " _-()").strip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        arc_name = f"{safe_name}_save_{timestamp}.zip"
        try:
            arc_path = self.get_saves_backup_path(create=True) / arc_name
            self._zip_folder(save_dir, arc_path)

            size_str = get_dir_size_str(arc_path)
            self.logger.info(f"Save backup created: {arc_path}")
            self.refresh_saves_list()
            self.set_status(self.t('msg_saves_zipped').format(arc_name))
            messagebox.showinfo("OK", self.t('save_zip_created').format(arc_name, size_str))
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))

    def restore_selected_save_archive(self):
        if not self.selected_save_archive or not self.selected_save_archive.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('msg_pick_archive'))
            return

        pp = self.get_selected_profile_path()
        if not pp:
            messagebox.showerror(self.t('dlg_error'), self.t('pick_profile'))
            return

        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        save_dir = self.profile_item_path('save')
        arc = self.selected_save_archive
        marker = self.read_slot_marker(arc)
        if marker is not None:
            # Архів одного слота: відновлюємо лише save\<слот>
            slot = str(marker.get('slot') or "")
            if not re.fullmatch(r'[\w.\- ]+', slot) or '..' in slot:
                messagebox.showerror(self.t('dlg_error'), self.t('msg_bad_slot_name').format(slot))
                return
            target = save_dir / slot
            confirm = self.t('slot_restore_confirm').format(arc.name, marker.get('title') or slot)
        else:
            target = save_dir
            confirm = self.t('save_zip_restore_confirm').format(arc.name)
        if self._is_in_steam_cloud(save_dir):
            confirm += self.t('cloud_restore_note')
        if not messagebox.askyesno(self.t('dlg_restore'), confirm):
            return

        tmp_dir = target.with_name(target.name + '.__restore_tmp')
        try:
            # Спершу розпаковуємо в тимчасову папку: якщо архів битий,
            # поточні сейви залишаються недоторканими.
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            with zipfile.ZipFile(arc, 'r') as zf:
                bad = zf.testzip()
                if bad:
                    raise zipfile.BadZipFile(self.t('msg_zip_corrupt').format(bad))
                zf.extractall(tmp_dir, members=[n for n in zf.namelist() if n != self.SLOT_MARKER])

            # Безпечний бекап перед заміною
            if target.exists():
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                if marker is not None:
                    safety_bak = self.get_saves_backup_path(create=True) / f"_pre_restore_slot_{target.name}_{stamp}.zip"
                    self._zip_folder(target, safety_bak, {"slot": target.name, "title": marker.get('title') or target.name,
                                                          "game": self.current_game})
                else:
                    safety_bak = self.get_saves_backup_path(create=True) / f"_pre_restore_backup_{stamp}.zip"
                    self._zip_folder(target, safety_bak)
                shutil.rmtree(target)

            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir.rename(target)
            self.refresh_saves_list()
            self.refresh_save_slots()
            self.refresh_profile_card()

            self.logger.info(f"Restored saves from {arc} into {target}")
            self.set_status(self.t('save_zip_restored'))
            messagebox.showinfo("OK", self.t('save_zip_restored'))
        except Exception as e:
            # Тимчасову копію прибираємо, лише якщо старі сейви ще на місці
            if target.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            messagebox.showerror(self.t('dlg_error'), str(e))

    def delete_selected_save_archive(self):
        if not self.selected_save_archive or not self.selected_save_archive.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('msg_pick_archive'))
            return

        if messagebox.askyesno(self.t('dlg_delete'), self.t('msg_delete_archive').format(self.selected_save_archive.name)):
            try:
                os.remove(self.selected_save_archive)
                self.selected_save_archive = None
                self.refresh_saves_list()
            except Exception as e:
                messagebox.showerror(self.t('dlg_error'), str(e))

    # --- ІНТЕРНЕТ-РАДІО ---
    def _radio_file(self) -> Path:
        return self.get_active_game_path() / 'live_streams.sii'

    def refresh_radio(self):
        self.radio_unit, self.radio_stations = "_nameless.radio", []
        self.radio_status = getattr(self, 'radio_status', {})
        f = self._radio_file()
        if f.exists():
            try:
                self.radio_unit, self.radio_stations = read_live_streams(f)
            except Exception as e:
                self.lbl_radio_summary.configure(text=f"⚠ live_streams.sii: {e}", text_color=COLOR_DANGER)
        self.my_radio = load_my_radio(self.current_game)
        langs = sorted({s['lang'].strip().upper() for s in self.radio_stations if s['lang'].strip()})
        values = [self.t('radio_all_langs')] + langs
        current = self.cb_radio_lang.get()
        self.cb_radio_lang.configure(values=values)
        self.cb_radio_lang.set(current if current in values else values[0])
        self.render_radio_list()

    def _radio_is_mine(self, st: dict) -> bool:
        return any(m['url'] == st['url'] for m in getattr(self, 'my_radio', []))

    def render_radio_list(self):
        tree = self.radio_tree
        tree.delete(*tree.get_children())
        stations = getattr(self, 'radio_stations', [])
        kw = self.entry_radio_filter.get().strip().lower()
        lang = self.cb_radio_lang.get()
        all_langs = lang == self.t('radio_all_langs') or lang not in self.cb_radio_lang.cget("values")
        rows = []
        for i, st in enumerate(stations):
            if not all_langs and st['lang'].strip().upper() != lang:
                continue
            if kw and not any(kw in (st[f] or "").lower() for f in ("name", "genre", "url", "lang")):
                continue
            rows.append((i, st))
        if self.radio_sort_key:
            key, rev = self.radio_sort_key
            def sort_val(item):
                st = item[1]
                if key == "bitrate":
                    try:
                        return int(st['bitrate'])
                    except ValueError:
                        return -1
                if key == "fav":
                    return st['favorite'] == "1"
                if key == "status":
                    return self.radio_status.get(st['url'], (None, ""))[1]
                return (st.get(key) or "").lower()
            rows.sort(key=sort_val, reverse=rev)
        for i, st in rows:
            ok, desc = self.radio_status.get(st['url'], (None, ""))
            tags = []
            if self._radio_is_mine(st):
                tags.append("mine")
            if ok is True:
                tags.append("alive")
            elif ok is False:
                tags.append("dead")
            status = ("✓ " if ok else "✗ ") + desc if ok is not None else ""
            tree.insert("", "end", iid=str(i), tags=tags, values=(
                "★" if st['favorite'] == "1" else "", st['name'], st['genre'], st['lang'], st['bitrate'], status, st['url']))

        favs = sum(1 for s in stations if s['favorite'] == "1")
        mine_present = sum(1 for s in stations if self._radio_is_mine(s))
        mine_lost = len(getattr(self, 'my_radio', [])) - mine_present
        text = self.t('radio_summary').format(len(stations), favs, len(getattr(self, 'my_radio', [])), len(rows))
        if mine_lost > 0:
            text += "\n" + self.t('radio_mine_lost').format(mine_lost)
        if not self._radio_file().exists():
            text = self.t('radio_no_file')
        self.lbl_radio_summary.configure(text=text, text_color="#FBBF24" if mine_lost > 0 else COLOR_TEXT_WHITE)

    def _radio_sort(self, col: str):
        cur = self.radio_sort_key
        self.radio_sort_key = (col, not cur[1]) if cur and cur[0] == col else (col, col in ("fav", "bitrate"))
        self.render_radio_list()

    def _radio_selected(self) -> list:
        return [int(iid) for iid in self.radio_tree.selection()]

    def _radio_save(self) -> bool:
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return False
        f = self._radio_file()
        try:
            if f.exists():
                self._backup_config(f)
            write_live_streams(f, self.radio_unit, self.radio_stations)
            save_my_radio(self.current_game, self.my_radio)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return False
        self.logger.info(f"live_streams.sii saved: {len(self.radio_stations)} stations")
        return True

    def radio_add_station(self):
        if not self._radio_file().parent.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('msg_dir_not_found').format(self._radio_file().parent))
            return
        dlg = RadioStationDialog(self, self.t)
        self.wait_window(dlg)
        st = dlg.result
        if not st:
            return
        if any(s['url'] == st['url'] for s in self.radio_stations):
            messagebox.showwarning("!", self.t('radio_duplicate'))
            return
        self.radio_stations.insert(0, st)
        self.my_radio = [m for m in self.my_radio if m['url'] != st['url']] + [st]
        if self._radio_save():
            self.render_radio_list()
            self.set_status(self.t('radio_added').format(st['name']))

    def radio_search_online(self):
        if not self._radio_file().parent.exists():
            messagebox.showerror(self.t('dlg_error'), self.t('msg_dir_not_found').format(self._radio_file().parent))
            return
        dlg = RadioSearchDialog(self, self.t, self._radio_add_many,
                                lambda: {s['url'] for s in self.radio_stations},
                                default_country="UA" if self.lang == "uk" else "")
        self.wait_window(dlg)

    def _radio_add_many(self, stations: list) -> int:
        """Додає станції нагору списку гри і до «моїх»; повертає, скільки додано."""
        present = {s['url'] for s in self.radio_stations}
        new = []
        for s in stations:
            if s['url'] not in present:
                present.add(s['url'])
                new.append({f: str(s.get(f, "")) for f in RADIO_FIELDS})
        if not new:
            return 0
        old_stations, old_mine = self.radio_stations, self.my_radio
        urls = {s['url'] for s in new}
        self.radio_stations = new + self.radio_stations
        self.my_radio = [m for m in self.my_radio if m['url'] not in urls] + new
        if not self._radio_save():
            self.radio_stations, self.my_radio = old_stations, old_mine
            return 0
        self.refresh_radio()  # оновить і список мов у фільтрі
        self.set_status(self.t('rsearch_added').format(len(new)))
        return len(new)

    def radio_toggle_favorite(self):
        sel = self._radio_selected()
        if not sel:
            return
        make_fav = any(self.radio_stations[i]['favorite'] != "1" for i in sel)
        for i in sel:
            self.radio_stations[i]['favorite'] = "1" if make_fav else "0"
            for m in self.my_radio:
                if m['url'] == self.radio_stations[i]['url']:
                    m['favorite'] = self.radio_stations[i]['favorite']
        if self._radio_save():
            self.render_radio_list()
            for i in sel:
                if self.radio_tree.exists(str(i)):
                    self.radio_tree.selection_add(str(i))

    def radio_delete_selected(self):
        sel = self._radio_selected()
        if not sel:
            messagebox.showinfo("!", self.t('radio_pick'))
            return
        names = "\n".join(self.radio_stations[i]['name'] for i in sel[:15]) + ("\n…" if len(sel) > 15 else "")
        if not messagebox.askyesno(self.t('dlg_delete'), self.t('radio_delete_confirm').format(len(sel), names)):
            return
        urls = {self.radio_stations[i]['url'] for i in sel}
        self.radio_stations = [s for i, s in enumerate(self.radio_stations) if i not in set(sel)]
        self.my_radio = [m for m in self.my_radio if m['url'] not in urls]
        if self._radio_save():
            self.render_radio_list()
            self.set_status(self.t('radio_deleted').format(len(sel)))

    def radio_reapply_mine(self):
        present = {s['url'] for s in self.radio_stations}
        lost = [dict(m) for m in self.my_radio if m['url'] not in present]
        if not lost:
            messagebox.showinfo("OK", self.t('radio_nothing_to_reapply'))
            return
        self.radio_stations = lost + self.radio_stations
        if self._radio_save():
            self.render_radio_list()
            messagebox.showinfo("OK", self.t('radio_reapplied').format(len(lost)))

    def radio_check_selected(self):
        sel = self._radio_selected()
        if not sel:
            messagebox.showinfo("!", self.t('radio_pick'))
            return
        self._radio_check([self.radio_stations[i]['url'] for i in sel])

    def radio_check_all(self):
        urls = [self.radio_stations[int(iid)]['url'] for iid in self.radio_tree.get_children()]
        self._radio_check(urls)

    def _radio_check(self, urls: list):
        if getattr(self, '_radio_check_running', False) or not urls:
            return
        self._radio_check_running = True
        self._radio_check_results = {}
        total = len(urls)
        self.radio_buttons['btn_radio_check_all'].configure(state="disabled")
        self.radio_buttons['btn_radio_check'].configure(state="disabled")

        def work():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=16) as pool:
                for url, res in zip(urls, pool.map(check_radio_stream, urls)):
                    self._radio_check_results[url] = res

        threading.Thread(target=work, daemon=True).start()

        def poll():
            done = dict(self._radio_check_results)
            self.radio_status.update(done)
            self.set_status(self.t('radio_checking').format(len(done), total))
            if len(done) < total:
                self.after(400, poll)
                return
            self._radio_check_running = False
            self.radio_buttons['btn_radio_check_all'].configure(state="normal")
            self.radio_buttons['btn_radio_check'].configure(state="normal")
            dead = sum(1 for ok, _ in done.values() if not ok)
            self.render_radio_list()
            self.set_status(self.t('radio_check_done').format(total - dead, dead), ok=dead == 0)

        self.after(400, poll)

    # --- СЕРВЕР КОНВОЮ ---
    def refresh_server_info(self):
        f = self.get_active_game_path() / 'server_config.sii'
        if not f.exists():
            self.lbl_server_info.configure(text=self.t('server_missing').format(f))
            self.btn_server_edit.configure(state="disabled")
            return
        try:
            v = read_server_config(f)
            self.lbl_server_info.configure(text=self.t('server_summary').format(
                v.get('lobby_name', '?'), v.get('max_players', '?'),
                self.t('yes') if v.get('password') else self.t('no'),
                len(v.get('moderator_list', [])), f))
            self.btn_server_edit.configure(state="normal")
        except Exception as e:
            self.lbl_server_info.configure(text=f"⚠ {e}")
            self.btn_server_edit.configure(state="disabled")

    def edit_server_config(self):
        f = self.get_active_game_path() / 'server_config.sii'
        try:
            values = read_server_config(f)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return
        dlg = ServerConfigDialog(self, values, self.t)
        self.wait_window(dlg)
        if not dlg.result or dlg.result == values:
            return
        try:
            self._backup_config(f)
            write_server_config(f, dlg.result)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return
        self.logger.info(f"server_config.sii updated: {f}")
        self.refresh_server_info()
        self.set_status(self.t('server_saved'))
        messagebox.showinfo("OK", self.t('server_saved'))

    # --- АНАЛІЗАТОР ЛОГІВ ---
    def get_mod_names(self) -> dict:
        """Назви модів з active_mods усіх профілів гри: {workshop id (dec) | пакет: назва}."""
        names = {}
        for prof in self.profiles:
            p = self.profile_item_path('profile.sii', prof)
            data = read_sii_file(p) if p else None
            for entry in (data or {}).get('active_mods', []) or []:
                pkg, _, name = entry.partition('|')
                if not name:
                    continue
                names[pkg] = name
                if pkg.startswith('mod_workshop_package.'):
                    try:
                        names[str(int(pkg.split('.', 1)[1], 16))] = name
                    except ValueError:
                        pass
        return names

    def refresh_log_analyzer(self):
        game_path = self.get_active_game_path()
        log_file = game_path / 'game.log.txt'
        self.log_lines = []
        self.log_groups = []
        self.game_version = ""

        if log_file.exists():
            try:
                self.log_lines = log_file.read_text(encoding='utf-8', errors='ignore').splitlines()
            except Exception as e:
                self.lbl_log_stats.configure(text=self.t('msg_log_read_error').format(e), text_color=COLOR_DANGER)
        if self.log_lines:
            errors = sum(1 for l in self.log_lines if "<ERROR>" in l)
            warnings = sum(1 for l in self.log_lines if "<WARNING>" in l)
            m = next((re.search(r'init ver\.([\w.]+)', l) for l in self.log_lines if 'init ver.' in l), None)
            self.game_version = m.group(1) if m else ""
            self.log_groups = group_log_problems(self.log_lines, self.get_mod_names())
            stats = self.t('log_stats').format(errors, warnings)
            if self.game_version:
                stats += f"  ·  {self.t('log_game_version').format(self.game_version)}"
            self.lbl_log_stats.configure(text=stats, text_color="#F87171" if errors else "#10B981")
        elif log_file.exists() is False:
            self.lbl_log_stats.configure(text=self.t('log_missing'), text_color=COLOR_TEXT_MUTED)

        self.crashes = self.load_crashes()
        self.net_info = self.load_net_info()
        self.refresh_crash_summary()
        self.filter_log_view()

    def refresh_crash_summary(self):
        crash_file = self.get_active_game_path() / 'game.crash.txt'
        if not crash_file.exists():
            self.lbl_crash_summary.configure(text=self.t('crash_none'), text_color="#10B981")
            return
        try:
            crashes = parse_crash_log(crash_file.read_text(encoding='utf-8', errors='ignore').replace('\0', ''))
        except Exception as e:
            self.lbl_crash_summary.configure(text=f"game.crash.txt: {e}", text_color=COLOR_DANGER)
            return
        if not crashes:
            self.lbl_crash_summary.configure(text=self.t('crash_none'), text_color="#10B981")
            return

        last = crashes[-1]
        when = last['when'].strftime("%d.%m.%Y %H:%M") if last['when'] else "?"
        counts = {}
        for c in crashes:
            code = c['exception'].split()[-1] if c['exception'] else "?"
            counts[code] = counts.get(code, 0) + 1
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        gpu = re.sub(r'\s+Mem:\S+', '', last['gpu']).replace(' Ver: ', self.t('crash_driver'))

        lines = [
            self.t('crash_total').format(len(crashes), top),
            self.t('crash_last').format(when, last['build'] or "?", last['exception'] or "?", last['fault_module'] or "?"),
        ]
        if gpu:
            lines.append(f"🖥 {gpu}")
        third = sorted({m for c in crashes[-5:] for m in c['third_party']})
        if third:
            lines.append(self.t('crash_third_party').format(", ".join(third)))
        outdated = bool(self.game_version and last['build'] and last['build'] != self.game_version)
        if outdated:
            lines.append(self.t('crash_outdated').format(self.game_version))
        self.lbl_crash_summary.configure(text="\n".join(lines), text_color=COLOR_TEXT_MUTED if outdated else "#FCA5A5")

    def filter_log_view(self):
        kw = self.entry_log_filter.get().strip().lower()
        values = list(self.seg_log_view.cget("values"))
        view = values.index(self.seg_log_view.get()) if self.seg_log_view.get() in values else 0
        box = self.txt_log_errors
        box.delete("1.0", "end")
        # Пояснення переносимо по словах; сирі рядки логу — без переносу
        box.configure(wrap="word" if view in (0, 3, 4) else "none")

        if view == 3:
            self._render_crashes(box)
            return
        if view == 4:
            self._render_network(box)
            return
        if not getattr(self, 'log_lines', None):
            box.insert("end", self.t('log_missing'), "muted")
            return

        if view == 0:
            groups = [g for g in self.log_groups if not kw or kw in g['title'].lower() or kw in g['sample'].lower()]
            if not groups:
                box.insert("end", self.t('log_clean'), "muted")
                return
            box.insert("end", self.t('log_groups_head').format(len(groups)) + "\n\n", "muted")
            for g in groups:
                icon = "📦" if g['kind'] == 'mod' else "⚙"
                counts = []
                if g['errors']:
                    counts.append(self.t('log_n_errors').format(g['errors']))
                if g['warnings']:
                    counts.append(self.t('log_n_warnings').format(g['warnings']))
                box.insert("end", f"{icon} {g['title']}", "head_mod" if g['kind'] == 'mod' else "head_game")
                box.insert("end", f"   {' · '.join(counts)}\n", "err" if g['errors'] else "warn")
                box.insert("end", f"     {g['sample'][:220]}\n\n", "muted")
            return

        level = "<ERROR>" if view == 1 else "<WARNING>"
        lines = [l for l in self.log_lines if level in l and (not kw or kw in l.lower())]
        total = sum(1 for l in self.log_lines if level in l)
        if not total:
            box.insert("end", self.t('log_clean'), "muted")
            return
        box.insert("end", self.t('log_shown').format(len(lines), total) + "\n\n", "muted")
        box.insert("end", "\n".join(lines), "err" if level == "<ERROR>" else "warn")

    # --- ДІАГНОСТИКА ЗБОЇВ І МЕРЕЖІ ---
    def _insert_link(self, box, text: str, url: str):
        """Клікабельне посилання в CTkTextbox (відкривається в браузері)."""
        import webbrowser
        self._link_seq = getattr(self, '_link_seq', 0) + 1
        tag = f"link{self._link_seq}"
        box.insert("end", text, ("link", tag))
        box.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
        box.tag_bind(tag, "<Enter>", lambda e: box.configure(cursor="hand2"))
        box.tag_bind(tag, "<Leave>", lambda e: box.configure(cursor="xterm"))

    def _insert_search_links(self, box, query: str, indent: str = "     "):
        box.insert("end", indent)
        self._insert_link(box, "🔎 Google", web_search_url(query))
        box.insert("end", "   ")
        self._insert_link(box, self.t('search_scs_forum'), web_search_url(query, "forum.scssoft.com"))
        box.insert("end", "   ")
        self._insert_link(box, "🔎 Steam", web_search_url(query, "steamcommunity.com"))
        box.insert("end", "\n")

    def _game_short(self) -> str:
        return "ETS2" if self.current_game == "Euro Truck Simulator 2" else "ATS"

    def load_crashes(self) -> list:
        f = self.get_active_game_path() / 'game.crash.txt'
        if not f.exists():
            return []
        try:
            return parse_crash_log(f.read_text(encoding='utf-8', errors='ignore').replace('\0', ''))
        except Exception:
            return []

    def load_net_info(self) -> dict | None:
        f = self.get_active_game_path() / 'net.log'
        if not f.exists():
            return None
        try:
            return parse_net_log(f.read_text(encoding='utf-8', errors='ignore'))
        except Exception:
            return None

    def _render_crashes(self, box):
        crashes = self.crashes
        if not crashes:
            box.insert("end", self.t('crash_none'), "muted")
            return
        version = getattr(self, 'game_version', '')
        n = len(crashes)
        fmt = lambda c: c['when'].strftime("%d.%m.%Y %H:%M") if c['when'] else "?"
        in_current = sum(1 for c in crashes if version and c['build'] == version)
        box.insert("end", self.t('crash_head').format(n, fmt(crashes[0]), fmt(crashes[-1]), in_current, version or "?") + "\n", "head_mod")
        counts = {}
        for c in crashes:
            counts[c['exception'] or "?"] = counts.get(c['exception'] or "?", 0) + 1
        box.insert("end", "   " + ",  ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) + "\n\n", "muted")

        suspects = crash_suspects(crashes)
        if suspects:
            box.insert("end", self.t('crash_suspects_head') + "\n", "head_mod")
            box.insert("end", self.t('crash_suspects_hint') + "\n\n", "muted")
            for key, names, cnt, in_stack in suspects:
                box.insert("end", f"  {'🔥' if in_stack else '•'} {self.t('km_' + key)}", "err" if in_stack else "warn")
                box.insert("end", f"  ({names})  —  {self.t('crash_in_n').format(cnt, n)}", "muted")
                box.insert("end", (f"  {self.t('crash_in_stack')}" if in_stack else "") + "\n", "err")
                box.insert("end", f"     {self.t('km_' + key + '_tip')}\n", "muted")
                first = names.split(",")[0].strip()
                self._insert_search_links(box, f"{self._game_short()} crash {first}")
            box.insert("end", "\n")

        box.insert("end", self.t('crash_recent_head') + "\n\n", "head_mod")
        for c in reversed(crashes[-8:]):
            code = (c['exception'] or "?").split()[0]
            name = c['exception'].split()[1] if len(c['exception'].split()) > 1 else ""
            old = bool(version and c['build'] and c['build'] != version)
            box.insert("end", f"  💥 {fmt(c)}  ·  {c['build'] or '?'}  ·  {c['exception'] or '?'}  ·  {c['fault_module'] or '?'}\n",
                       "muted" if old else "err")
            box.insert("end", f"     {self.t(EXCEPTION_KEYS.get(code, 'exc_other'))}\n", "muted")
            if c.get('avail_phys') is not None and c['avail_phys'] < 2048:
                box.insert("end", f"     {self.t('crash_low_mem').format(c['avail_phys'])}\n", "warn")
            if c['third_party']:
                box.insert("end", f"     {self.t('crash_stack_mods').format(', '.join(c['third_party']))}\n", "warn")
            if old:
                box.insert("end", f"     {self.t('crash_old_version')}\n", "muted")
            major = ".".join((c['build'] or "").split(".")[:2])
            self._insert_search_links(box, f"{self._game_short()} {major} crash {code} {name} {c['fault_module']}".strip())
            box.insert("end", "\n")

    def _render_network(self, box):
        info = self.net_info
        results = getattr(self, 'net_check_results', None)
        if results is not None:
            box.insert("end", self.t('net_check_head') + "\n", "head_mod")
            for ok, what, detail in results:
                box.insert("end", f"  {'✓' if ok else '✗'} {what}  —  {detail}\n", "ok" if ok else "err")
            bad = [r for r in results if not r[0]]
            box.insert("end", ("  " + (self.t('net_check_bad') if bad else self.t('net_check_good')) + "\n\n"),
                       "warn" if bad else "muted")
        elif getattr(self, '_net_check_running', False):
            box.insert("end", self.t('net_checking') + "\n\n", "muted")
        if info is None:
            box.insert("end", self.t('net_no_log'), "muted")
            return
        box.insert("end", self.t('net_head').format(info['sessions'], info['errors']) + "\n", "head_mod")
        box.insert("end", self.t('net_hint') + "\n\n", "muted")
        if not info['groups']:
            box.insert("end", self.t('net_no_errors'), "muted")
            return
        for g in info['groups']:
            box.insert("end", f"  {self.t(g['kind'])}", "warn")
            box.insert("end", f"   ×{g['count']}   {g['first'][:10]} … {g['last'][:10]}\n", "muted")
            if g['hosts']:
                box.insert("end", f"     {', '.join(sorted(g['hosts'])[:4])}\n", "muted")
            box.insert("end", f"     {self.t(g['kind'] + '_tip')}\n", "ok")
            self._insert_search_links(box, f"{self._game_short()} {self.t(g['kind'] + '_query')}")
            box.insert("end", "\n")

    def run_net_check(self):
        if getattr(self, '_net_check_running', False):
            return
        self._net_check_running = True
        self.net_check_results = None
        self.btn_net_check.configure(state="disabled")
        holder = {}
        threading.Thread(target=lambda: holder.update(r=run_network_check()), daemon=True).start()
        values = list(self.seg_log_view.cget("values"))
        self.seg_log_view.set(values[4])
        self.filter_log_view()

        def poll():
            if "r" not in holder:
                self.after(300, poll)
                return
            self._net_check_running = False
            self.net_check_results = holder["r"]
            try:
                self.btn_net_check.configure(state="normal")
                self.filter_log_view()
                bad = sum(1 for r in holder["r"] if not r[0])
                self.set_status(self.t('net_check_bad') if bad else self.t('net_check_good'), ok=not bad)
            except Exception:
                pass
        self.after(300, poll)

    def _sanitize(self, text: str) -> str:
        """Прибирає з тексту особисті шляхи: залишає лише імена файлів."""
        text = text.replace(str(Path.home()), "%USERPROFILE%")
        return re.sub(r"[A-Za-z]:[\\/][^'\"\n|]*[\\/]", "", text)

    def build_diagnostic_report(self) -> str:
        t = self.t
        version = getattr(self, 'game_version', '') or "?"
        lines = [f"{APP_NAME} {APP_VERSION} — {t('report_title')}", "",
                 f"{t('report_game')}: {self.current_game} {version}"]
        crashes = self.crashes
        last = crashes[-1] if crashes else None
        if last:
            lines.append(f"OS: {last['os'] or '?'}")
            gpu = re.sub(r'\s+Mem:\S+', '', last['gpu']).replace(' Ver: ', t('crash_driver'))
            lines.append(f"GPU: {gpu}")
        mods = getattr(self, 'installed_mods', None)
        if self.get_selected_profile_path() is not None:
            active = self._profile_active_mods()
            lines.append(t('report_mods').format(len(active)))
            if mods is not None:
                missing = [name for pkg, name in active if pkg not in mods]
                outdated = [self._mod_label(pkg, name) for pkg, name in active if mods.get(pkg, {}).get('compatible') is False]
                if missing:
                    lines.append(t('report_missing').format(", ".join(missing)))
                if outdated:
                    lines.append(t('report_outdated').format(", ".join(outdated)))
        lines.append("")
        errors = sum(1 for l in getattr(self, 'log_lines', []) if "<ERROR>" in l)
        warnings = sum(1 for l in getattr(self, 'log_lines', []) if "<WARNING>" in l)
        lines.append(t('report_log').format(errors, warnings))
        for g in getattr(self, 'log_groups', [])[:6]:
            lines.append(f"  - {g['title']}: E{g['errors']} W{g['warnings']}")
        lines.append("")
        if crashes:
            lines.append(t('report_crashes').format(len(crashes)))
            for c in crashes[-3:]:
                when = c['when'].strftime("%d.%m.%Y %H:%M") if c['when'] else "?"
                lines.append(f"  - {when} {c['build']} {c['exception']} @ {c['fault_module']}"
                             + (f" [stack: {', '.join(c['third_party'])}]" if c['third_party'] else ""))
            sus = crash_suspects(crashes)
            if sus:
                lines.append("  " + t('report_suspects') + ": " + "; ".join(
                    f"{t('km_' + k)} ({names}) {cnt}/{len(crashes)}{' STACK' if st else ''}" for k, names, cnt, st in sus))
        else:
            lines.append(t('crash_none'))
        if self.net_info and self.net_info['groups']:
            lines.append("")
            lines.append(t('report_net').format(self.net_info['errors']))
            for g in self.net_info['groups'][:6]:
                lines.append(f"  - {t(g['kind'])}: {g['count']} ({g['last'][:10]})")
        return self._sanitize("\n".join(lines))

    def copy_diagnostic_report(self):
        report = self.build_diagnostic_report()
        self.clipboard_clear()
        self.clipboard_append(report)
        self.set_status(self.t('report_copied'))
        messagebox.showinfo("OK", self.t('report_copied') + "\n\n" + report[:1500])

    def open_game_log_in_editor(self):
        log_file = self.get_active_game_path() / 'game.log.txt'
        if log_file.exists():
            os.startfile(log_file)
        else:
            messagebox.showinfo("!", self.t('log_missing'))

    def open_crash_log_in_editor(self):
        crash_file = self.get_active_game_path() / 'game.crash.txt'
        if crash_file.exists():
            os.startfile(crash_file)
        else:
            messagebox.showinfo("!", self.t('crash_none'))

    # --- КЕШ ТА МОДИ ---
    def refresh_cache_status(self):
        cache_path = self.get_active_game_path() / 'cache'
        size_str = get_dir_size_str(cache_path)
        self.lbl_cache_size.configure(
            text=self.t('clean_cache_size').format(size_str),
            text_color=UA_YELLOW if cache_path.exists() else COLOR_TEXT_MUTED
        )
        self.btn_clean_current.configure(text=self.t('clean_current_btn').format(self.current_game))

    def refresh_mods_info(self):
        mod_dir = self.get_active_game_path() / 'mod'
        ws_dir = find_workshop_dir(self.current_game)
        lines = [self.t('mods_folder_line').format(mod_dir)]
        if ws_dir:
            lines.append(self.t('mods_workshop_line').format(ws_dir))
        self.lbl_mods_info.configure(text="\n".join(lines))
        self.refresh_mods_diag()

    def open_mods_folder(self):
        mod_dir = self.get_active_game_path() / 'mod'
        if not self.get_active_game_path().exists():
            messagebox.showwarning("!", self.t('msg_folder_missing').format(self.get_active_game_path()))
            return
        mod_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(mod_dir)

    def open_workshop_subscriptions(self):
        import webbrowser
        appid = STEAM_APPS.get(self.current_game, 227300)
        webbrowser.open(f"https://steamcommunity.com/my/myworkshopfiles/?appid={appid}&browsefilter=mysubscriptions")

    # Сканування модів іде у фоновому потоці; UI оновлюється лише з головного потоку (опитування через after)
    def refresh_mods_diag(self):
        if getattr(self, '_mods_thread', None) and self._mods_thread.is_alive():
            self._mods_rescan = True
            return
        self._mods_rescan = False
        game, game_path = self.current_game, self.get_active_game_path()
        version = getattr(self, 'game_version', '')
        self.lbl_mods_summary.configure(text=self.t('mods_scanning'), text_color=COLOR_TEXT_MUTED)
        self._mods_result = None

        def work():
            try:
                mods = scan_installed_mods(game, game_path)
                for m in mods.values():
                    m['compatible'] = workshop_compatible(m['path'], version) if m['source'] == 'workshop' else None
                need = [m['ws_id'] for m in mods.values() if m['ws_id'] and not m['name']]
                cache = load_workshop_title_cache()
                missing = [wid for wid in need if wid not in cache]
                if missing:
                    fetched = fetch_workshop_titles(missing)
                    if fetched:
                        cache.update(fetched)
                        save_workshop_title_cache(cache)
                for m in mods.values():
                    if m['ws_id'] and not m['name']:
                        m['name'] = cache.get(m['ws_id'])
                self._mods_result = (game, game_path, mods)
            except Exception as e:
                self._mods_result = (game, game_path, e)

        self._mods_thread = threading.Thread(target=work, daemon=True)
        self._mods_thread.start()
        self.after(150, self._poll_mods_diag)

    def _poll_mods_diag(self):
        if self._mods_result is None:
            self.after(150, self._poll_mods_diag)
            return
        game, game_path, mods = self._mods_result
        if self._mods_rescan or game != self.current_game or game_path != self.get_active_game_path():
            self._mods_thread = None
            self.refresh_mods_diag()
            return
        if isinstance(mods, Exception):
            self.installed_mods = {}
            self.lbl_mods_summary.configure(text=f"⚠ {mods}", text_color=COLOR_DANGER)
            return
        self.installed_mods = mods
        self.render_mods_view()
        self.refresh_save_slots()

    def _profile_active_mods(self, profile=None) -> list:
        p = self.profile_item_path('profile.sii', profile)
        data = read_sii_file(p) if p else None
        result = []
        for entry in (data or {}).get('active_mods', []) or []:
            pkg, _, name = entry.partition('|')
            result.append((pkg, name or pkg))
        return result

    def _mod_label(self, pkg: str, fallback: str = "") -> str:
        m = getattr(self, 'installed_mods', {}).get(pkg)
        name = fallback or (m and m.get('name'))
        if not name and m and m.get('ws_id'):
            name = f"Workshop #{m['ws_id']}"
        return name or pkg

    # --- АВТОСОРТУВАННЯ МОДІВ ---
    def mods_auto_sort(self):
        if self.get_selected_profile_path() is None:
            messagebox.showinfo("!", self.t('tweak_no_profile'))
            return
        mods = getattr(self, 'installed_mods', None)
        if mods is None:
            messagebox.showinfo("!", self.t('mods_scanning'))
            return
        if is_game_running(self.current_game):
            messagebox.showwarning(self.t('dlg_warning'), self.t('modsort_game_running'))
            return
        display = list(reversed(self._profile_active_mods()))
        if len(display) < 2:
            messagebox.showinfo("!", self.t('modsort_too_few'))
            return
        self.btn_mods_sort.configure(state="disabled")
        self.set_status(self.t('modsort_analyzing'))
        labels = [self._mod_label(pkg, name) for pkg, name in display]
        box = {}

        def work():
            try:
                ws_ids = [mods[p]['ws_id'] for p, _ in display if p in mods and mods[p]['ws_id']]
                tags = get_workshop_tags(ws_ids) if ws_ids else {}
                items = []
                for (pkg, _name), label in zip(display, labels):
                    m = mods.get(pkg)
                    cats = read_mod_categories(m['path']) if m else []
                    cat, src = classify_mod(cats, tags.get(m['ws_id'], []) if m and m['ws_id'] else [], label)
                    items.append({'pkg': pkg, 'name': label, 'cat': cat, 'src': src, 'missing': m is None})
                box['r'] = (items, plan_mod_sort(items))
            except Exception as e:
                box['r'] = e

        threading.Thread(target=work, daemon=True).start()

        def poll():
            if 'r' not in box:
                self.after(150, poll)
                return
            self.btn_mods_sort.configure(state="normal")
            self._show_ready_status()  # прибрати «Аналізую моди...», якщо перегляд просто закриють
            if isinstance(box['r'], Exception):
                messagebox.showerror(self.t('dlg_error'), str(box['r']))
                return
            self._show_mod_sort(display, *box['r'])
        self.after(150, poll)

    def _show_mod_sort(self, display: list, items: list, plan: dict):
        order, n = plan['order'], len(items)
        moved = sum(1 for new, old in enumerate(order) if new != old)
        if not moved:
            self.set_status(self.t('modsort_already'))
            messagebox.showinfo("OK", self.t('modsort_already'))
            return
        in_family = {i for fam in plan['families'] for i in fam}
        unknown = sum(1 for it in items if it['cat'] is None)
        lines = [(self.t('modsort_preview_head') + "\n", "muted")]
        last_tier = None
        for new, old in enumerate(order):
            tier = plan['tier'][old]
            if tier != last_tier:
                lines.append((f"\n── {self.t('modsort_tier_' + MOD_SORT_TIERS[tier][0])} ──\n", "head"))
                last_tier = tier
            it = items[old]
            arrow, tag = ("↑", "up") if new < old else ("↓", "down") if new > old else (" ", "")
            marks = ("🔗" if old in in_family else "  ") + ("?" if it['src'] in (None, 'name') else " ")
            was = self.t('modsort_was').format(old + 1) if new != old else ""
            lines.append((f"{new + 1:>3}. {arrow} {marks} {it['name'][:62]:<62} {was}\n", tag))
        lines.append(("\n" + self.t('modsort_legend') + "\n", "muted"))
        summary = self.t('modsort_summary').format(n, moved, len(plan['families']), unknown)
        dlg = ModSortDialog(self, self.t, summary, lines)
        self.wait_window(dlg)
        if not dlg.result:
            return
        self._apply_mod_sort(display, order)

    def _apply_mod_sort(self, display: list, order: list):
        if is_game_running(self.current_game):
            messagebox.showwarning(self.t('dlg_warning'), self.t('modsort_game_running'))
            return
        path = self.profile_item_path('profile.sii')
        n = len(display)
        try:
            if list(reversed(self._profile_active_mods())) != display:
                raise ValueError(self.t('modsort_changed'))
            raw = path.read_bytes()
            # Новий порядок задано згори вниз; у файлі active_mods[0] — найнижчий
            file_order = [n - 1 - order[n - 1 - k] for k in range(n)]
            new = reorder_profile_mods(raw, file_order)
            self._backup_config(path)
            path.write_bytes(new)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return
        self._mod_sort_undo = (path, raw)
        self.btn_mods_sort_undo.grid()
        self.logger.info(f"Mods auto-sorted: {path}")
        self.render_mods_view()
        self.set_status(self.t('modsort_done'))
        messagebox.showinfo("OK", self.t('modsort_done_msg'))

    def mods_sort_undo(self):
        undo = getattr(self, '_mod_sort_undo', None)
        if not undo:
            return
        path, raw = undo
        if is_game_running(self.current_game):
            messagebox.showwarning(self.t('dlg_warning'), self.t('modsort_game_running'))
            return
        if not messagebox.askyesno(self.t('dlg_warning'), self.t('modsort_undo_confirm').format(path)):
            return
        try:
            path.write_bytes(raw)
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))
            return
        self._mod_sort_undo = None
        self.btn_mods_sort_undo.grid_remove()
        self.render_mods_view()
        self.set_status(self.t('modsort_undone'))

    def render_mods_view(self):
        box = self.txt_mods
        box.delete("1.0", "end")
        mods = getattr(self, 'installed_mods', None)
        if mods is None:
            box.insert("end", self.t('mods_scanning'), "muted")
            return

        kw = self.entry_mods_filter.get().strip().lower()
        values = list(self.seg_mods_view.cget("values"))
        view = values.index(self.seg_mods_view.get()) if self.seg_mods_view.get() in values else 0
        has_profile = self.get_selected_profile_path() is not None
        active = self._profile_active_mods() if has_profile else []
        used_anywhere = {pkg for prof in self.profiles for pkg, _ in self._profile_active_mods(prof)}
        missing = [(pkg, name) for pkg, name in active if pkg not in mods]
        outdated = [(pkg, name) for pkg, name in active if mods.get(pkg, {}).get('compatible') is False]
        unused = sorted((m for pkg, m in mods.items() if pkg not in used_anywhere), key=lambda m: -m['size'])

        total = sum(m['size'] for m in mods.values())
        n_ws = sum(1 for m in mods.values() if m['source'] == 'workshop')
        summary = self.t('mods_summary').format(
            len(active), len(mods) - n_ws, n_ws, fmt_size(total),
            len(missing), len(outdated), len(unused), fmt_size(sum(m['size'] for m in unused)))
        self.lbl_mods_summary.configure(text=summary, text_color="#F87171" if missing else COLOR_TEXT_WHITE)

        def match(*parts):
            return not kw or any(kw in (p or "").lower() for p in parts)

        def src_tag(m):
            if not m:
                return ""
            return "Workshop" if m['source'] == 'workshop' else "mod"

        if view == 0:
            if not has_profile:
                box.insert("end", self.t('tweak_no_profile'), "muted")
                return
            box.insert("end", self.t('mods_order_head').format(len(active)) + "\n\n", "muted")
            # У profile.sii active_mods[0] — найнижчий пріоритет, а гра показує найвищий згори
            for i, (pkg, name) in enumerate(reversed(active), 1):
                m = mods.get(pkg)
                label = self._mod_label(pkg, name)
                if not match(label, pkg):
                    continue
                if m is None:
                    mark, tag, note = "❌", "err", self.t('mods_note_missing')
                elif m.get('compatible') is False:
                    mark, tag, note = "⚠", "warn", self.t('mods_note_outdated').format(self.game_version)
                else:
                    mark, tag, note = "  ", "ok", ""
                info = f"{src_tag(m):<8} {fmt_size(m['size']):>9}" if m else ""
                box.insert("end", f"{mark} {i:>3}. {label[:60]:<60} {info}", tag)
                box.insert("end", f"  {note}\n" if note else "\n", "muted" if not note else tag)
            return

        if view == 1:
            if not missing and not outdated:
                box.insert("end", self.t('mods_no_problems'), "muted")
                return
            if missing:
                box.insert("end", self.t('mods_missing_head').format(len(missing)) + "\n", "head")
                box.insert("end", self.t('mods_missing_hint') + "\n\n", "muted")
                for pkg, name in missing:
                    if match(name, pkg):
                        box.insert("end", f"  ❌ {name}\n", "err")
                        box.insert("end", f"       {pkg}\n", "muted")
                box.insert("end", "\n")
            if outdated:
                box.insert("end", self.t('mods_outdated_head').format(len(outdated), self.game_version) + "\n", "head")
                box.insert("end", self.t('mods_outdated_hint') + "\n\n", "muted")
                for pkg, name in outdated:
                    label = self._mod_label(pkg, name)
                    if match(label, pkg):
                        box.insert("end", f"  ⚠ {label}\n", "warn")
            return

        # view == 2: невикористані
        if not unused:
            box.insert("end", self.t('mods_no_unused'), "muted")
            return
        box.insert("end", self.t('mods_unused_head').format(len(unused), fmt_size(sum(m['size'] for m in unused))) + "\n", "head")
        box.insert("end", self.t('mods_unused_hint') + "\n\n", "muted")
        for m in unused:
            label = self._mod_label(m['pkg'])
            if not match(label, m['pkg'], m.get('ws_id')):
                continue
            note = f"  ⚠ {self.t('mods_note_outdated').format(self.game_version)}" if m.get('compatible') is False else ""
            box.insert("end", f"  {label[:60]:<60} {src_tag(m):<8} {fmt_size(m['size']):>9}", "ok")
            box.insert("end", f"{note}\n", "warn")

    def clean_cache_current(self):
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        cache = self.get_active_game_path() / 'cache'
        if not cache.exists():
            messagebox.showinfo(self.t('dlg_info'), self.t('msg_cache_clean_already'))
            return

        if not messagebox.askyesno(self.t('dlg_cleanup'), self.t('clean_confirm').format(cache)):
            return

        try:
            shutil.rmtree(cache)
            self.logger.info(f"Cache deleted: {cache}")
            self.set_status(self.t('clean_ok'))
            self.refresh_cache_status()
            messagebox.showinfo("OK", self.t('clean_ok'))
        except Exception as e:
            messagebox.showerror(self.t('dlg_error'), str(e))

    def clean_cache_both(self):
        if any(is_game_running(g) for g in GAMES):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return

        if not messagebox.askyesno(self.t('dlg_cleanup_both'), self.t('msg_clean_both_confirm')):
            return

        results = []
        for g_name in GAMES:
            g_path = self.settings.get_game_path(g_name)
            cache = g_path / 'cache'
            if cache.exists():
                try:
                    shutil.rmtree(cache)
                    results.append(self.t('msg_cache_cleaned').format(g_name))
                except Exception as e:
                    results.append(self.t('msg_cache_clean_failed').format(g_name, e))
            else:
                results.append(self.t('msg_cache_was_clean').format(g_name))

        self.refresh_cache_status()
        self.set_status(self.t('msg_cache_done'))
        messagebox.showinfo(self.t('dlg_result'), "\n".join(results))

    # --- АВТОБЕКАПИ ГРИ ТА СКРИНШОТИ ---
    def refresh_cleanup_info(self):
        game_path = self.get_active_game_path()
        baks = list_game_auto_backups(game_path)
        keep = int(self.cb_bak_keep.get())
        for b in baks:
            b['size'] = get_dir_size(b['path'])
        total = sum(b['size'] for b in baks)
        old = baks[keep:]
        freed = sum(b['size'] for b in old)

        if baks:
            self.lbl_bak_summary.configure(
                text=self.t('bak_summary').format(len(baks), fmt_size(total), len(old), fmt_size(freed)))
            lines = []
            for i, b in enumerate(baks):
                mark = "✓" if i < keep else "✗"
                date = datetime.fromtimestamp(b['path'].stat().st_mtime).strftime("%d.%m.%Y")
                lines.append(f"{mark} {b['version']:<14} {date}  {fmt_size(b['size']):>9}")
            self.lbl_bak_list.configure(text="\n".join(lines))
        else:
            self.lbl_bak_summary.configure(text=self.t('bak_none'))
            self.lbl_bak_list.configure(text="")
        self.btn_bak_clean.configure(state="normal" if old else "disabled")

        shots = game_path / 'screenshot'
        files = [f for f in shots.iterdir() if f.is_file()] if shots.is_dir() else []
        self.lbl_shots_info.configure(
            text=self.t('shots_info').format(len(files), fmt_size(sum(f.stat().st_size for f in files)), shots))

    def clean_game_auto_backups(self):
        if is_game_running(self.current_game):
            if not messagebox.askyesno(self.t('dlg_warning'), self.t('warning_game_running')):
                return
        keep = int(self.cb_bak_keep.get())
        old = list_game_auto_backups(self.get_active_game_path())[keep:]
        if not old:
            return
        names = "\n".join(b['path'].name for b in old)
        if not messagebox.askyesno(self.t('dlg_cleanup'), self.t('bak_confirm').format(len(old), keep, names)):
            return

        ok = send_to_recycle_bin([b['path'] for b in old])
        left = [b for b in old if b['path'].exists()]
        self.logger.info(f"Game auto-backups to recycle bin: {[b['path'].name for b in old]}, ok={ok}, left={len(left)}")
        self.refresh_cleanup_info()
        if left:
            messagebox.showerror(self.t('dlg_error'), self.t('bak_failed').format("\n".join(b['path'].name for b in left)))
        else:
            self.set_status(self.t('bak_done').format(len(old)))
            messagebox.showinfo("OK", self.t('bak_done').format(len(old)))

    def open_screenshots_folder(self):
        shots = self.get_active_game_path() / 'screenshot'
        if shots.is_dir():
            os.startfile(shots)
        else:
            messagebox.showinfo("!", self.t('msg_dir_not_found').format(shots))

    # --- ЗАГАЛЬНІ НАЛАШТУВАННЯ ТА ШЛЯХИ ---
    def on_language_selected(self, value: str):
        lang = "uk" if value == "UA" else "en"
        if lang != self.lang:
            self.set_language(lang)

    def set_language(self, lang: str):
        self.lang = lang
        self.settings.set_language(self.lang)
        self.rebuild_ui()
        self.fetch_steam_sales()

    def toggle_language(self):
        self.set_language("en" if self.lang == "uk" else "uk")

    # Перемикачі всередині вкладок: (атрибут, дія після відновлення вибору)
    SUB_VIEWS = (("seg_saves_view", "_show_saves_panel"), ("seg_mods_view", "render_mods_view"),
                 ("seg_log_view", "filter_log_view"))

    def rebuild_ui(self):
        """Перебудовує інтерфейс (після зміни мови): усі підписи беруться з перекладу заново.
        Вкладка, профіль і вибір у перемикачах зберігаються."""
        tab_names = list(self.tabview._segmented_button._buttons_dict.keys())
        tab_idx = tab_names.index(self.tabview.get()) if self.tabview.get() in tab_names else 0
        sub = {}
        for attr, _ in self.SUB_VIEWS:
            seg = getattr(self, attr)
            values = list(seg.cget("values"))
            sub[attr] = values.index(seg.get()) if seg.get() in values else 0

        for w in self.winfo_children():
            w.destroy()
        self.title(f"{self.t('title')} v{APP_VERSION}")
        self._build_ui()
        self.tabview.set(self.t(TAB_KEYS[tab_idx]))
        self.refresh_all()
        self._show_ready_status()
        for attr, action in self.SUB_VIEWS:
            seg = getattr(self, attr)
            seg.set(list(seg.cget("values"))[sub[attr]])
            getattr(self, action)()

    def on_game_switched(self, game_name: str):
        self.current_game = game_name
        self.settings.set_last_game(game_name)
        self.refresh_all()
        self._show_ready_status()

    def browse_game_folder(self):
        current = self.get_active_game_path()
        init_dir = str(current) if current.exists() else str(get_documents_dir())

        new_dir = filedialog.askdirectory(
            title=self.t('dlg_pick_game_folder').format(self.current_game),
            initialdir=init_dir
        )
        if not new_dir:
            return

        new_path = Path(new_dir)
        has_cfg = (new_path / 'config.cfg').exists()
        has_profiles = (new_path / 'profiles').exists() or (new_path / 'steam_profiles').exists()

        if not (has_cfg or has_profiles):
            confirm = messagebox.askyesno(
                "Увага",
                self.t('msg_folder_not_game_data').format(new_path)
            )
            if not confirm:
                return

        self.settings.set_game_path(self.current_game, str(new_path))
        self.logger.info(f"Custom path set for {self.current_game}: {new_path}")
        self.set_status(self.t('msg_path_updated').format(new_path.name))
        self.refresh_all()

    def browse_specific_path(self, game_name: str):
        current = self.settings.get_game_path(game_name)
        new_dir = filedialog.askdirectory(
            title=self.t('dlg_pick_game_folder').format(game_name),
            initialdir=str(current) if current.exists() else str(get_documents_dir())
        )
        if new_dir:
            self.settings.set_game_path(game_name, new_dir)
            self.refresh_all()

    def commit_typed_path(self, entry, game_name: str):
        typed = entry.get().strip().strip('"')
        if not typed or Path(typed) == self.settings.get_game_path(game_name):
            return
        self.settings.set_game_path(game_name, typed)
        self.logger.info(f"Path typed for {game_name}: {typed}")
        self.set_status(self.t('msg_path_updated').format(Path(typed).name))
        self.refresh_all()

    def _paths_source_text(self) -> str:
        short = {"Euro Truck Simulator 2": "ETS 2", "American Truck Simulator": "ATS"}
        return "\n".join(
            f"{short[g]}: {self.t('path_src_' + self.settings.get_path_source(g))}" for g in GAMES
        )

    def reset_paths_to_default(self):
        # Порожній шлях = автовизначення; кеш скидаємо, щоб підхопити свіжі параметри Steam
        self.settings._auto_cache.clear()
        for g in GAMES:
            self.settings.set_game_path(g, "")
        details = "\n\n".join(
            f"{g}:\n{self.settings.get_game_path(g)}\n({self.t('path_src_' + self.settings.get_path_source(g))})"
            for g in GAMES
        )
        self.refresh_all()
        messagebox.showinfo("OK", self.t('reset_paths_done').format(details))

    def open_current_game_folder(self):
        p = self.get_active_game_path()
        if p.exists():
            os.startfile(p)
        else:
            messagebox.showwarning("!", self.t('msg_folder_missing').format(p))

    def open_presets_directory(self):
        self._open_app_folder(self.get_presets_path)

    def open_logs_directory(self):
        os.startfile(get_logs_dir())

    def fetch_steam_sales(self):
        self.lbl_steam_sales.configure(text=self.t('steam_checking'), text_color="#9CA3AF")

        def task():
            cc = "ua" if self.lang == "uk" else "us"
            lang_param = "ukrainian" if self.lang == "uk" else "english"
            sales = []

            for name, appid in STEAM_APPS.items():
                try:
                    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc={cc}&l={lang_param}"
                    req = urllib.request.Request(url, headers={'User-Agent': f'TruckControlsManager/{APP_VERSION}'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                    info = data.get(str(appid), {})
                    if not info.get("success"):
                        continue
                    po = (info.get("data") or {}).get("price_overview") or {}
                    pct = int(po.get("discount_percent") or 0)
                    if pct > 0:
                        short_name = "ETS2" if "Euro" in name else "ATS"
                        price = po.get("final_formatted") or po.get("final") or ""
                        sales.append(f"{short_name}: -{pct}% ({price})")
                except Exception:
                    pass

            try:
                self.after(0, lambda: self._update_steam_label(sales))
            except Exception:
                pass

        threading.Thread(target=task, daemon=True).start()

    def _update_steam_label(self, sales: list):
        if sales:
            text = self.t('steam_sales_prefix') + "  |  ".join(sales)
            self.lbl_steam_sales.configure(text=text, text_color=UA_YELLOW)
        else:
            self.lbl_steam_sales.configure(text=self.t('steam_none'), text_color=COLOR_TEXT_MUTED)

    def open_steam_link(self):
        appid = STEAM_APPS.get(self.current_game, 227300)
        import webbrowser
        webbrowser.open(f"https://store.steampowered.com/app/{appid}/")

    def _show_ready_status(self):
        short = "ETS 2" if self.current_game == "Euro Truck Simulator 2" else "ATS"
        version = getattr(self, 'game_version', '')
        self.set_status(self.t('ready_status').format(f"{short} {version}".strip(), len(self.profiles)))

    def set_status(self, text: str, ok: bool = True):
        self.lbl_status.configure(
            text=text,
            text_color="#10B981" if ok else "#EF4444"
        )


if __name__ == "__main__":
    app = TruckControlsApp()
    app.mainloop()
