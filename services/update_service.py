from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import hashlib
import json
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


def installer_destination(update_url: str, latest_version: str, *, save_only: bool) -> Path:
    file_name = _installer_name(update_url, latest_version)
    folders = []
    if save_only:
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                    value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                    folders.append(Path(os.path.expandvars(value)))
            except OSError:
                pass
        folders.append(Path.home() / "Downloads")
    folders.extend([Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "DdokddakProduction3" / "updates",
                    Path(tempfile.gettempdir()) / "DdokddakProduction3" / "updates"])
    for folder in dict.fromkeys(folders):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            # Each attempt owns its directory: an old running installer cannot lock this file.
            attempt = Path(tempfile.mkdtemp(prefix="download-", dir=folder))
            (attempt / 'production3-download.json').write_text(json.dumps({
                'application':'DdokddakProduction3','version':str(latest_version).lstrip('vV'),
                'installer':file_name,'created_at':time.time(),
            }),encoding='utf-8')
            return attempt / file_name
        except OSError:
            continue
    raise OSError("저장 가능한 폴더가 없습니다. 다운로드 폴더 권한과 디스크 여유 공간을 확인해 주세요.")


def validate_installer(path: Path) -> None:
    with path.open("rb") as source:
        header = source.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError("설치파일 형식이 올바르지 않습니다. 보안 차단 페이지 또는 불완전한 파일입니다.")
        offset = int.from_bytes(header[60:64], "little")
        if offset < 64 or offset > path.stat().st_size - 4:
            raise ValueError("설치파일이 손상되었습니다. 다시 다운로드해 주세요.")
        source.seek(offset)
        if source.read(4) != b"PE\0\0":
            raise ValueError("Windows 설치파일 검증에 실패했습니다.")


def _release_checksum_url(response):
    for item in [*response.history, response]:
        parsed = urlparse(item.url)
        if (parsed.hostname == "github.com" and
                parsed.path.startswith("/interojo-160907/3-ddokddak/releases/download/") and
                parsed.path.endswith(".exe")):
            return "https://github.com" + parsed.path + ".sha256"
    return None


def download_installer(update_url: str, destination: Path, progress: ProgressCallback) -> Path:
    target = str(update_url or "").strip()
    if not target.lower().startswith(("https://", "http://")):
        raise ValueError("관리 시트에 올바른 설치파일 주소가 등록되지 않았습니다.")
    destination = Path(destination)
    for attempt in range(3):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix="installer-", suffix=".part", dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                with requests.get(target, stream=True, timeout=(10, 90),
                        headers={"User-Agent": "Ddokddak-Production3-Updater"}) as response:
                    response.raise_for_status()
                    checksum_url = _release_checksum_url(response)
                    total = int(response.headers.get("Content-Length") or 0)
                    received = 0
                    digest = hashlib.sha256()
                    progress(0 if total else -1)
                    for chunk in response.iter_content(chunk_size=512 * 1024):
                        if chunk:
                            output.write(chunk)
                            digest.update(chunk)
                            received += len(chunk)
                            if total:
                                progress(min(99, int(received * 100 / total)))
                if total and received != total:
                    raise requests.exceptions.ChunkedEncodingError("설치파일 다운로드가 중간에 끊겼습니다.")
            validate_installer(temporary)
            if checksum_url:
                with requests.get(checksum_url, timeout=(10, 30)) as checksum:
                    checksum.raise_for_status()
                    expected = checksum.text.strip().split()[0] if checksum.text.strip() else ""
                    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or digest.hexdigest() != expected.lower():
                        raise ValueError("공식 설치파일의 SHA256 검증에 실패했습니다. 다시 다운로드해 주세요.")
            temporary.replace(destination)
            progress(100)
            return destination
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = not isinstance(exc, requests.exceptions.SSLError) and (status is None or status in (408, 429) or status >= 500)
            if attempt == 2 or not retryable:
                raise OSError("다운로드 서버에 연결하지 못했습니다. 사내망의 GitHub 다운로드 허용 여부를 확인한 뒤 다시 시도해 주세요.") from exc
            progress(-1)
            time.sleep(attempt + 1)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise OSError("다운로드를 완료하지 못했습니다.")


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _restart_command():
    return str(Path(sys.executable).resolve()), ([] if getattr(sys, "frozen", False)
        else [str(Path(__file__).resolve().parents[1] / "gui_app_pyside6.py")])


def schedule_install_and_restart(installer_path: Path) -> None:
    if sys.platform != "win32":
        raise OSError("자동 업데이트는 Windows에서만 사용할 수 있습니다.")

    validate_installer(installer_path)
    handshake = Path(tempfile.mkdtemp(prefix="update-start-"))
    ready = handshake / "ready"
    failure = handshake / "error.txt"
    restart_program, restart_arguments = _restart_command()
    restart_argument_text = ", ".join(
        _powershell_quote(subprocess.list2cmdline([argument])) for argument in restart_arguments
    )
    parent_pid = os.getpid()
    script = (
        "$ErrorActionPreference = 'Stop'\ntry {\n"
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "Add-Type -AssemblyName System.Drawing\n"
        "$createdNew = $false\n"
        "$mutex = New-Object System.Threading.Mutex($true, 'Local\\DdokddakProduction3.Update', [ref]$createdNew)\n"
        "if (-not $createdNew) { throw '다른 업데이트가 이미 실행 중입니다.' }\n"
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
        f"[System.IO.File]::WriteAllText({_powershell_quote(str(ready))}, 'ready')\n"
        "$frames = @('●  ○  ○','○  ●  ○','○  ○  ●')\n"
        "$frameIndex = 0\n"
        "$deadline = (Get-Date).AddSeconds(30)\n"
        "while ((Get-Process -Id $parentPid -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {\n"
        "  $spinner.Text = $frames[$frameIndex % $frames.Count]; $frameIndex++\n"
        "  [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 140\n"
        "}\n"
        "if (Get-Process -Id $parentPid -ErrorAction SilentlyContinue) { throw '기존 프로그램이 종료되지 않았습니다. 설치파일을 직접 실행해 주세요.' }\n"
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
        "  throw ('설치 실패: 종료 코드 ' + $installProcess.ExitCode)\n"
        "}\n"
        "} catch {\n"
        f"  [System.IO.File]::WriteAllText({_powershell_quote(str(failure))}, [string]$_)\n"
        "  [System.Windows.Forms.MessageBox]::Show(('업데이트를 완료하지 못했습니다. 설치파일을 직접 실행해 주세요. ' + [string]$_), '업데이트 오류') | Out-Null\n"
        "} finally { if ($createdNew -and $mutex) { $mutex.ReleaseMutex(); $mutex.Dispose() } }\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    helper = subprocess.Popen(
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

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if failure.exists():
            raise OSError(failure.read_text(encoding="utf-8-sig"))
        if ready.exists():
            return
        if helper.poll() is not None:
            raise OSError("업데이트 실행기가 시작되지 않았습니다. 저장된 설치파일을 직접 실행해 주세요.")
        time.sleep(0.1)
    helper.terminate()
    raise OSError("업데이트 실행기 시작 시간이 초과되었습니다. 다시 시도하거나 설치파일을 직접 실행해 주세요.")
