from __future__ import annotations

import base64
import os
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
    parent_pid = os.getpid()
    script = (
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "Add-Type -AssemblyName System.Drawing\n"
        "$createdNew = $false\n"
        "$mutex = New-Object System.Threading.Mutex($true, 'Local\\DdokddakProduction3.Update', [ref]$createdNew)\n"
        "if (-not $createdNew) { exit }\n"
        f"$installer = {_powershell_quote(str(installer_path.resolve()))}\n"
        f"$parentPid = {parent_pid}\n"
        "$form = New-Object System.Windows.Forms.Form\n"
        "$form.Text = '똑딱이 업데이트'\n"
        "$form.Size = New-Object System.Drawing.Size(560,280)\n"
        "$form.StartPosition = 'CenterScreen'\n"
        "$form.FormBorderStyle = 'FixedDialog'\n"
        "$form.MaximizeBox = $false\n"
        "$form.MinimizeBox = $false\n"
        "$form.ControlBox = $false\n"
        "$form.TopMost = $true\n"
        "$form.BackColor = [System.Drawing.Color]::White\n"
        "$title = New-Object System.Windows.Forms.Label\n"
        "$title.Text = '새 버전을 적용하고 있습니다'\n"
        "$title.Font = New-Object System.Drawing.Font('Malgun Gothic',16,[System.Drawing.FontStyle]::Bold)\n"
        "$title.ForeColor = [System.Drawing.Color]::FromArgb(16,35,63)\n"
        "$title.Location = New-Object System.Drawing.Point(82,26)\n"
        "$title.Size = New-Object System.Drawing.Size(430,36)\n"
        "$form.Controls.Add($title)\n"
        "$spinner = New-Object System.Windows.Forms.Label\n"
        "$spinner.Text = '●  ○  ○'\n"
        "$spinner.Font = New-Object System.Drawing.Font('Malgun Gothic',12,[System.Drawing.FontStyle]::Bold)\n"
        "$spinner.ForeColor = [System.Drawing.Color]::FromArgb(22,119,255)\n"
        "$spinner.Location = New-Object System.Drawing.Point(34,34)\n"
        "$spinner.Size = New-Object System.Drawing.Size(48,26)\n"
        "$form.Controls.Add($spinner)\n"
        "$stage = New-Object System.Windows.Forms.Label\n"
        "$stage.Text = '1 / 4  ·  종료 준비'\n"
        "$stage.Font = New-Object System.Drawing.Font('Malgun Gothic',9,[System.Drawing.FontStyle]::Bold)\n"
        "$stage.ForeColor = [System.Drawing.Color]::FromArgb(22,119,255)\n"
        "$stage.Location = New-Object System.Drawing.Point(36,75)\n"
        "$stage.Size = New-Object System.Drawing.Size(475,24)\n"
        "$form.Controls.Add($stage)\n"
        "$status = New-Object System.Windows.Forms.Label\n"
        "$status.Text = '프로그램을 안전하게 종료하는 중입니다.'\n"
        "$status.Font = New-Object System.Drawing.Font('Malgun Gothic',10)\n"
        "$status.ForeColor = [System.Drawing.Color]::FromArgb(64,86,109)\n"
        "$status.Location = New-Object System.Drawing.Point(36,103)\n"
        "$status.Size = New-Object System.Drawing.Size(475,28)\n"
        "$form.Controls.Add($status)\n"
        "$progress = New-Object System.Windows.Forms.ProgressBar\n"
        "$progress.Location = New-Object System.Drawing.Point(36,142)\n"
        "$progress.Size = New-Object System.Drawing.Size(475,18)\n"
        "$progress.Style = 'Marquee'\n"
        "$progress.MarqueeAnimationSpeed = 24\n"
        "$form.Controls.Add($progress)\n"
        "$caption = New-Object System.Windows.Forms.Label\n"
        "$caption.Text = '창을 닫지 마세요. 완료되면 최신 버전이 자동으로 실행됩니다.'\n"
        "$caption.Font = New-Object System.Drawing.Font('Malgun Gothic',9)\n"
        "$caption.ForeColor = [System.Drawing.Color]::FromArgb(10,103,216)\n"
        "$caption.Location = New-Object System.Drawing.Point(36,180)\n"
        "$caption.Size = New-Object System.Drawing.Size(475,25)\n"
        "$form.Controls.Add($caption)\n"
        "$form.Show()\n"
        "$form.Activate()\n"
        "[System.Windows.Forms.Application]::DoEvents()\n"
        "$frames = @('●  ○  ○','○  ●  ○','○  ○  ●')\n"
        "$frameIndex = 0\n"
        "$deadline = (Get-Date).AddSeconds(30)\n"
        "while ((Get-Process -Id $parentPid -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {\n"
        "  $spinner.Text = $frames[$frameIndex % $frames.Count]; $frameIndex++\n"
        "  [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 140\n"
        "}\n"
        "$stage.Text = '2 / 4  ·  설치 준비'\n"
        "$status.Text = '설치파일을 준비하고 있습니다.'\n"
        "1..4 | ForEach-Object { $spinner.Text = $frames[$frameIndex % $frames.Count]; $frameIndex++; [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 120 }\n"
        "$stage.Text = '3 / 4  ·  업데이트 적용 중'\n"
        "$status.Text = '새 버전 설치 중입니다.'\n"
        "[System.Windows.Forms.Application]::DoEvents()\n"
        "$installProcess = Start-Process -FilePath $installer -WindowStyle Hidden "
        "-ArgumentList @('/VERYSILENT','/SP-','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS','/NOCANCEL') "
        "-PassThru\n"
        "$installWatch = [System.Diagnostics.Stopwatch]::StartNew()\n"
        "while (-not $installProcess.HasExited) {\n"
        "  $installProcess.Refresh()\n"
        "  $spinner.Text = $frames[$frameIndex % $frames.Count]; $frameIndex++\n"
        "  if ($installWatch.Elapsed.TotalSeconds -ge 15) { $status.Text = '설치를 마무리하고 있습니다.' }\n"
        "  [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 140\n"
        "}\n"
        "if ($installProcess.ExitCode -eq 0) {\n"
        "  $progress.Style = 'Continuous'; $progress.Value = 100\n"
        "  $spinner.Text = '●  ●  ●'\n"
        "  $stage.Text = '4 / 4  ·  완료 및 재실행'\n"
        "  $status.Text = '업데이트가 완료되었습니다.'\n"
        "  $caption.Text = '최신 버전을 실행합니다.'\n"
        "  [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 900\n"
        "  $form.Close()\n"
        f"  Start-Process -FilePath {_powershell_quote(restart_program)}"
        + (f" -ArgumentList @({restart_argument_text})" if restart_arguments else "")
        + "\n} else {\n"
        "  $progress.Style = 'Continuous'; $progress.Value = 0\n"
        "  $spinner.Text = '!  !  !'\n"
        "  $stage.Text = '업데이트 중단'\n"
        "  $stage.ForeColor = [System.Drawing.Color]::FromArgb(180,35,24)\n"
        "  $status.Text = '업데이트를 완료하지 못했습니다.'\n"
        "  $status.ForeColor = [System.Drawing.Color]::FromArgb(180,35,24)\n"
        "  $caption.Text = '설치파일을 다시 내려받아 실행해 주세요.'\n"
        "  [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Seconds 4\n"
        "  $form.Close()\n"
        "}\n"
        "$mutex.ReleaseMutex(); $mutex.Dispose()\n"
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
