"""Real installer helper integration; disposable CI runner only."""
import os,sys,tempfile,subprocess,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Disposable GitHub runner only')
if sys.argv[1]=='child':
    from unittest.mock import patch
    from services.update_service import schedule_install_and_restart
    with patch('services.update_service._restart_command',return_value=(sys.executable,[sys.argv[3]])):
        schedule_install_and_restart(Path(sys.argv[2]))
    raise SystemExit(0)
root=Path(tempfile.mkdtemp(prefix='real-updater-'))
old=root/'prior';old.mkdir()
subprocess.run(['gh','release','download','v3.2.5','--repo','interojo-160907/3-ddokddak','--pattern','ddokddak-production3-setup.exe','--dir',str(old)],check=True)
app=root/'app'
subprocess.run([str(old/'ddokddak-production3-setup.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/TASKS=',f'/DIR={app}'],check=True,timeout=180)
import winreg
with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Interojo\DdokddakProduction3') as key:
    data=Path(winreg.QueryValueEx(key,'DataRoot')[0])
sentinel=data/'settings'/'helper-sentinel.txt';sentinel.parent.mkdir(exist_ok=True,parents=True);sentinel.write_text('preserve')
marker=root/'restarted.txt';restart=root/'restart.py';restart.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("restarted")\n')
subprocess.run([sys.executable,__file__,'child',str(Path(sys.argv[1]).resolve()),str(restart)],check=True,timeout=30)
deadline=time.monotonic()+180
while not marker.exists() and time.monotonic()<deadline:time.sleep(.5)
assert marker.exists(),'Updater failed to install and restart'
assert sentinel.read_text()=='preserve'
report=root/'smoke.json'
subprocess.run([str(app/'gui_app_pyside6.exe'),'--package-smoke-test',str(report)],check=True,timeout=60)
import json
assert json.loads(report.read_text())['app_version']=='3.2.6'
print('PASS: real updater helper, parent exit, 3.2.5 -> 3.2.6 installation, restart, data preservation')
