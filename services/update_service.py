from __future__ import annotations

import base64
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable
from urllib.parse import unquote, urlparse

import requests


ProgressCallback = Callable[[int], None]


def _installer_name(update_url: str, latest_version: str) -> str:
    candidate = Path(unquote(urlparse(update_url).path)).name
    if not candidate.lower().endswith(".exe"):
        version = str(latest_version or "latest").strip().lstrip("vV") or "latest"
        candidate = f"ddokddak-production3-setup-{version}.exe"
    safe_name = re.sub(r"[^0-9A-Za-z._-]+", "-", candidate).strip(".-")
    return safe_name or "ddokddak-production3-setup.exe"


def installer_destination(
    update_url: str,
    latest_version: str,
    *,
    save_only: bool,
) -> Path:
    file_name = _installer_name(update_url, latest_version)
    folder = (
        Path.home() / "Downloads"
        if save_only
        else Path(tempfile.gettempdir()) / "DdokddakProduction3" / "updates"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder / file_name


def download_installer(
    update_url: str,
    destination: Path,
    progress: ProgressCallback,
) -> Path:
    target = str(update_url or "").strip()
    if not target.lower().startswith(("https://", "http://")):
        raise ValueError("관리 시트에 올바른 설치파일 주소가 등록되지 않았습니다.")

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with requests.get(
            target,
            stream=True,
            timeout=(10, 90),
            headers={"User-Agent": "Ddokddak-Production3-Updater"},
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            progress(0 if total else -1)
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=512 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    received += len(chunk)
                    if total:
                        progress(min(99, int(received * 100 / total)))
        if not temporary.exists() or temporary.stat().st_size <= 0:
            raise OSError("다운로드된 설치파일이 비어 있습니다.")
        destination.unlink(missing_ok=True)
        temporary.replace(destination)
        progress(100)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def schedule_install_and_restart(installer_path: Path) -> None:
    if sys.platform != "win32":
        raise OSError("자동 업데이트는 Windows에서만 사용할 수 있습니다.")

    restart_program = str(Path(sys.executable).resolve())
    restart_arguments = (
        []
        if getattr(sys, "frozen", False)
        else [str(Path(__file__).resolve().parents[1] / "gui_app_pyside6.py")]
    )
    restart_argument_text = ", ".join(
        _powershell_quote(argument) for argument in restart_arguments
    )
    script = (
        "Start-Sleep -Seconds 2\n"
        f"$installer = {_powershell_quote(str(installer_path.resolve()))}\n"
        "$result = Start-Process -FilePath $installer "
        "-ArgumentList @('/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS') "
        "-Wait -PassThru\n"
        "if ($result.ExitCode -eq 0) {\n"
        f"  Start-Process -FilePath {_powershell_quote(restart_program)}"
        + (f" -ArgumentList @({restart_argument_text})" if restart_arguments else "")
        + "\n}\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )
