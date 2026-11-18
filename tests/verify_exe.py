"""Checks that a built PyInstaller exe contains exactly the bytecode of app.py.

Usage:  python tests/verify_exe.py [path/to/TruckControlsManager.exe]
Run with the same Python version that built the exe. Exit code 0 = match.
"""
import marshal
import sys
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent


def normalize(code: types.CodeType):
    """Code object as nested tuples without the file name (it differs between build and here)."""
    consts = tuple(normalize(c) if isinstance(c, types.CodeType) else c for c in code.co_consts)
    return (code.co_name, code.co_qualname, code.co_code, consts, code.co_names, code.co_varnames,
            code.co_freevars, code.co_cellvars, code.co_argcount, code.co_kwonlyargcount,
            code.co_posonlyargcount, code.co_flags, code.co_firstlineno, code.co_linetable,
            code.co_exceptiontable)


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "TruckControlsManager.exe"
    reader = CArchiveReader(str(exe))
    built = marshal.loads(reader.extract("app"))
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    fresh = compile(source, str(ROOT / "app.py"), "exec")
    if normalize(built) == normalize(fresh):
        print(f"OK: {exe.name} bytecode matches app.py")
        return 0
    print(f"MISMATCH: {exe.name} was not built from the current app.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
