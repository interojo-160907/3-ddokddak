"""Fail the recovery release if its application differs from the v2.6.4 source."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "v2.6.4"
VERSION = "2.7.10"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def verify():
    prefixes = ("collectors/", "services/", "ui/", "styles/", "assets/", "installer/", "management/")
    singles = {"config.py", "gui_app_pyside6.py", "production3.spec", "requirements.txt"}
    # This module is executed only by --package-smoke-test, never normal startup.
    qa_only = {"services/package_smoke_test.py"}
    baseline = git("ls-tree", "-r", "--name-only", "-z", BASE).decode().split("\0")
    expected = {p for p in baseline if p.startswith(prefixes) or p in singles}
    actual = {p.relative_to(ROOT).as_posix() for prefix in prefixes
              for p in (ROOT / prefix).rglob("*") if p.is_file()
              and "__pycache__" not in p.parts and "output" not in p.parts}
    actual |= singles
    assert actual == expected, f"Runtime file set differs: {actual ^ expected}"
    for name in sorted(expected - qa_only):
        before = git("show", f"{BASE}:{name}").replace(b"\r\n", b"\n")
        after = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        if name == "config.py":
            before = before.replace(b'APP_VERSION = "2.6.4"', f'APP_VERSION = "{VERSION}"'.encode())
        elif name == "installer/production3.iss":
            before = before.replace(b'MyAppVersion "2.6.4"', f'MyAppVersion "{VERSION}"'.encode())
            # Packaging-only addition: also sign Inno's temporary self-copies.
            before = before.replace(
                b"VersionInfoVersion={#MyAppVersion}\n",
                b"VersionInfoVersion={#MyAppVersion}\nSignTool=ddokddak\nSignedUninstaller=yes\n",
            )
        elif name == "installer/version_info.txt":
            before = before.replace(b"2.6.4", VERSION.encode()).replace(b"(2, 6, 4, 0)", b"(2, 7, 10, 0)")
        assert after == before, f"Unexpected functional change from {BASE}: {name}"
    print(f"PASS: {len(expected - qa_only)} application/installer files match {BASE}; only release version {VERSION} and installer signing directives differ.")


if __name__ == "__main__":
    verify()
