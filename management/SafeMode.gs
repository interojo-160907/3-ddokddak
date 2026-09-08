// v2.6: 전체설정 A=허가 프로그램, B=최종 사용 권한, C=실제 모드.
// Existing user permission checks remain in Code.gs. No sheet cells are written.
function safeModeSettingsEdited(e) {
  if (!e || !e.range || e.range.getSheet().getName() !== '전체설정') return;
  if (e.range.getColumn() > 3 || e.range.getLastColumn() < 3) return;
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    for (let r = Math.max(2,e.range.getRow()); r <= e.range.getLastRow(); r++) {
      const program = text_(e.range.getSheet().getRange(r,1).getDisplayValue());
      if (program) PropertiesService.getScriptProperties().deleteProperty('safe_mode_selection:' + program);
    }
  } finally { lock.releaseLock(); }
}

function setupSafeModeTrigger() {
  const exists = ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'safeModeSettingsEdited');
  if (!exists) ScriptApp.newTrigger('safeModeSettingsEdited').forSpreadsheet(SPREADSHEET_ID).onEdit().create();
}

function safeModeControl_(spreadsheet, program) {
  const sheet = spreadsheet.getSheetByName('전체설정');
  if (!sheet) throw new Error('전체설정 탭이 없습니다.');
  const rows = sheet.getDataRange().getDisplayValues().slice(1);
  const found = rows.filter(row => text_(row[0]) === program);
  if (found.length !== 1) throw new Error('전체설정 프로그램 행은 정확히 하나여야 합니다.');
  const row = found[0];
  if (text_(row[1]) !== '허가') throw new Error('전체설정 최종 사용 권한을 확인해 주세요.');
  const mode = text_(row[2]);
  if (!['자동모드', '안전모드'].includes(mode)) throw new Error('C열 모드 값이 올바르지 않습니다.');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const properties = PropertiesService.getScriptProperties();
    const key = 'safe_mode_selection:' + program;
    if (mode === '자동모드') {
      properties.deleteProperty(key);
      return {mode: mode};
    }
    const saved = properties.getProperty(key);
    if (saved) return {mode: mode, safe_asset: JSON.parse(saved)};
    const repo = 'interojo-160907/3-ddokddak';
    const folder = '안전모드_APS자료';
    const headers = {Accept: 'application/vnd.github+json'};
    function get(url) {
      const response = UrlFetchApp.fetch(url, {headers: headers, muteHttpExceptions: true});
      if (response.getResponseCode() !== 200) throw new Error('Git 안전모드 자료 조회 실패: ' + response.getResponseCode());
      return JSON.parse(response.getContentText());
    }
    const meta = get('https://api.github.com/repos/' + repo);
    const commit = get('https://api.github.com/repos/' + repo + '/commits/' + encodeURIComponent(meta.default_branch)).sha;
    const files = get('https://api.github.com/repos/' + repo + '/contents/' + encodeURIComponent(folder) + '?ref=' + commit);
    const candidates = files.filter(file => {
      if (file.type !== 'file' || !/^\d{6}_(오전|오후)\.xlsx$/.test(file.name)) return false;
      const y = 2000 + Number(file.name.slice(0,2)), m = Number(file.name.slice(2,4)), d = Number(file.name.slice(4,6));
      const date = new Date(Date.UTC(y,m-1,d));
      return date.getUTCFullYear() === y && date.getUTCMonth() === m-1 && date.getUTCDate() === d;
    });
    candidates.sort((a,b) => {
      const ak = a.name.slice(0,6) + (a.name.includes('_오후') ? '1' : '0');
      const bk = b.name.slice(0,6) + (b.name.includes('_오후') ? '1' : '0');
      return ak.localeCompare(bk);
    });
    if (!candidates.length) throw new Error('사용 가능한 안전모드 엑셀 파일이 없습니다.');
    const file = candidates[candidates.length - 1];
    const asset = {
      name: file.name, git_sha: file.sha,
      url: 'https://raw.githubusercontent.com/' + repo + '/' + commit + '/' + encodeURIComponent(folder) + '/' + encodeURIComponent(file.name),
      selection_id: commit + ':' + file.sha,
      selected_at: new Date().toISOString()
    };
    properties.setProperty(key, JSON.stringify(asset));
    return {mode: mode, safe_asset: asset};
  } finally {
    lock.releaseLock();
  }
}
