const SPREADSHEET_ID = '18em-huYbzFRa_UDyQOomg5Z_eOhu-GBO2BY9BWjNZlg';
const USER_SHEET = '사용자관리';
const VERSION_SHEET = '버전관리';
const ONLINE = '🟢 사용중';
const OFFLINE = '🔴 미사용';
const STALE_MS = 60 * 60 * 1000;

function text_(value) {
  return String(value || '').trim();
}
function normalizeVersion_(value) {
  return text_(value).replace(/^v/i, '');
}
function column_(headers, names) {
  for (const name of names) {
    const index = headers.indexOf(name);
    if (index >= 0) return index;
  }
  throw new Error('필수 열 없음: ' + names.join(' / '));
}
function json_(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
function users_(spreadsheet) {
  const sheet = spreadsheet.getSheetByName(USER_SHEET);
  if (!sheet) throw new Error('사용자관리 탭을 찾을 수 없습니다.');
  const rows = sheet.getDataRange().getDisplayValues();
  const headers = rows.shift().map(text_);
  return {
    sheet: sheet, rows: rows,
    pc: column_(headers, ['PC 고유ID', 'PC 고유 ID']),
    program: column_(headers, ['허가 프로그램']),
    use: column_(headers, ['사용여부', '사용 여부'])
  };
}
function allowed_(row, useColumn) {
  return Boolean(row) &&
    ['Y', 'YES', 'TRUE', '사용'].includes(text_(row[useColumn]).toUpperCase());
}
function presenceKey_(pcId, program) {
  return 'presence:' + JSON.stringify([pcId, program]);
}
function doGet() {
  return json_({ok: true, service: '생산3공장 똑딱이 관리 API'});
}

// G열만 수정합니다. E열 권한과 버전관리 H2는 수정하지 않습니다.
function recordPresence_(spreadsheet, pcId, program, connected) {
  const lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    const u = users_(spreadsheet);
    if (text_(u.sheet.getRange(1, 7).getValue()) !== '접속여부') {
      throw new Error('사용자관리 G1은 접속여부여야 합니다.');
    }
    const index = u.rows.findIndex(row =>
      text_(row[u.pc]).toUpperCase() === pcId &&
      text_(row[u.program]) === program);
    if (index < 0) return false;
    const online = connected && allowed_(u.rows[index], u.use);
    const props = PropertiesService.getScriptProperties();
    const key = presenceKey_(pcId, program);
    if (online) {
      props.setProperty(key, String(Date.now()));
    } else {
      props.deleteProperty(key);
    }
    u.sheet.getRange(index + 2, 7).setValue(online ? ONLINE : OFFLINE);
    SpreadsheetApp.flush();
    return true;
  } finally {
    lock.releaseLock();
  }
}

function doPost(e) {
  try {
    const request = JSON.parse(e.postData.contents || '{}');
    const pcId = text_(request.pc_id).toUpperCase();
    const program = text_(request.program);
    const currentVersion = normalizeVersion_(request.version);
    if (!pcId || !program) {
      throw new Error('PC 고유 ID 또는 프로그램명이 없습니다.');
    }
    const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);

    // 모니터링 전용 요청은 권한/버전 판정과 분리합니다.
    if (request.action === 'presence') {
      if (typeof request.connected !== 'boolean') {
        throw new Error('connected는 true 또는 false여야 합니다.');
      }
      const recorded = recordPresence_(spreadsheet, pcId, program, request.connected);
      return json_({ok: recorded, result: {presence_recorded: recorded}});
    }

    const u = users_(spreadsheet);
    const user = u.rows.find(row =>
      text_(row[u.pc]).toUpperCase() === pcId &&
      text_(row[u.program]) === program);
    const allowed = allowed_(user, u.use);
    if (request.action === 'mode') {
      if (!allowed) throw new Error('등록되지 않았거나 사용이 중지된 PC입니다.');
      return json_({ok: true, result: safeModeControl_(spreadsheet, program)});
    }

    // 이전 설치판의 접속 신호도 받되, 기록 실패로 권한을 바꾸지 않습니다.
    if (typeof request.connected === 'boolean') {
      try {
        recordPresence_(spreadsheet, pcId, program, request.connected);
      } catch (presenceError) {
        console.error('접속 표시 오류: ' + presenceError.message);
      }
    }

    const versionSheet = spreadsheet.getSheetByName(VERSION_SHEET);
    if (!versionSheet) throw new Error('버전관리 탭을 찾을 수 없습니다.');
    const versionValues = versionSheet.getDataRange().getDisplayValues();
    const versionHeaders = versionValues.shift().map(text_);
    const versionProgramColumn = column_(versionHeaders, ['프로그램']);
    const latestColumn = column_(versionHeaders, ['최신버전', '최신 버전']);
    const installerColumn = column_(versionHeaders, ['설치파일', '설치 파일']);
    const versionRow = versionValues.find(row =>
      text_(row[versionProgramColumn]) === program);
    const latestVersion = versionRow ? text_(versionRow[latestColumn]) : '';
    const updateUrl = versionRow ? text_(versionRow[installerColumn]) : '';
    const updateRequired = Boolean(allowed && latestVersion &&
      normalizeVersion_(latestVersion) !== currentVersion);
    let message = '사용 권한 정상';
    if (!allowed) {
      message = '등록되지 않았거나 사용이 중지된 PC입니다.';
    } else if (updateRequired) {
      message = '최신 버전 ' + latestVersion + ' 업데이트가 필요합니다.';
    }
    return json_({
      ok: true,
      result: {
        allowed: allowed, latest_version: latestVersion,
        update_required: updateRequired, update_url: updateUrl,
        message: message, notices: []
      }
    });
  } catch (error) {
    return json_({
      ok: false,
      result: {
        allowed: false, reason: 'server_error',
        message: '관리시트 확인 오류: ' + error.message
      }
    });
  }
}

// 서버가 30분마다 실행합니다. PC의 시각 대신 서버 수신시각을 사용합니다.
function refreshPresence() {
  const lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    const u = users_(SpreadsheetApp.openById(SPREADSHEET_ID));
    if (text_(u.sheet.getRange(1, 7).getValue()) !== '접속여부') {
      throw new Error('사용자관리 G1은 접속여부여야 합니다.');
    }
    if (!u.rows.length) return;
    const props = PropertiesService.getScriptProperties().getProperties();
    const now = Date.now();
    const values = u.rows.map(row => {
      const pc = text_(row[u.pc]).toUpperCase();
      const program = text_(row[u.program]);
      if (!pc || !program) return [row[6] || ''];
      const last = Number(props[presenceKey_(pc, program)] || 0);
      const recent = last > 0 && now >= last && now - last < STALE_MS;
      return [allowed_(row, u.use) && recent ? ONLINE : OFFLINE];
    });
    u.sheet.getRange(2, 7, values.length, 1).setValues(values);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
}

// 편집기에서 이 함수를 한 번 실행해 자동 정리를 등록합니다.
function setupPresence() {
  refreshPresence();
  const exists = ScriptApp.getProjectTriggers().some(trigger =>
    trigger.getHandlerFunction() === 'refreshPresence');
  if (!exists) {
    ScriptApp.newTrigger('refreshPresence').timeBased().everyMinutes(30).create();
  }
  console.log('완료: 접속여부 자동 정리 등록 (30분 간격)');
}
