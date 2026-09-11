const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const props = new Map();
const rows = [['PC 고유ID','허가 프로그램','소속','사용자명','사용여부','비고','접속여부'],
  ['SAMEPC','생산3공장 똑딱이','','','Y','',''],
  ['SAMEPC','SCM 컨트롤타워','','','N','','']];
const sheet = {getDataRange:()=>({getDisplayValues:()=>rows.map(r=>r.slice())}),
  getRange:(r,c)=>({getValue:()=>rows[r-1][c-1],setValue:v=>{rows[r-1][c-1]=v;}})};
const versions = [['프로그램','최신버전','설치파일'],['생산3공장 똑딱이','2.6.2','production.exe'],['SCM 컨트롤타워','9.0.0','scm.exe']];
const spreadsheet={getSheetByName:n=>n==='사용자관리'?sheet:{getDataRange:()=>({getDisplayValues:()=>versions.map(r=>r.slice())})}};
const context={console,Date,SpreadsheetApp:{openById:()=>spreadsheet,flush(){}},
  LockService:{getScriptLock:()=>({waitLock(){},releaseLock(){}})},
  PropertiesService:{getScriptProperties:()=>({setProperty:(k,v)=>props.set(k,v),deleteProperty:k=>props.delete(k)})},
  ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/Code.gs','utf8'),context);
const request=program=>context.doPost({postData:{contents:JSON.stringify({pc_id:'SAMEPC',program,version:'2.6.2'})}}).result;
assert.equal(request('생산3공장 똑딱이').allowed,true);
assert.equal(request('생산3공장 똑딱이').latest_version,'2.6.2');
assert.equal(request('SCM 컨트롤타워').allowed,false);
assert.equal(request('SCM 컨트롤타워').update_url,'scm.exe');
context.recordPresence_(spreadsheet,'SAMEPC','생산3공장 똑딱이',true);
assert.equal(rows[1][6],'🟢 사용중');
assert.equal(rows[2][6],'');
context.recordPresence_(spreadsheet,'SAMEPC','SCM 컨트롤타워',false);
assert.equal(rows[1][6],'🟢 사용중');
assert.equal(rows[2][6],'🔴 미사용');
assert.notEqual(context.presenceKey_('SAMEPC','SCM 컨트롤타워'),context.presenceKey_('SAMEPC','생산3공장 똑딱이'));
console.log('PASS: shared PC has separate permissions, versions, installer links and presence');
