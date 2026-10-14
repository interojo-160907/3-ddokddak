const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const values=[['PC 고유ID','허가 프로그램','사용여부'],['YESPC','생산3공장 똑딱이','Y'],['NOPC','생산3공장 똑딱이','N'],['SCMPC','SCM 컨트롤타워','Y']];
let secret='test-only-reader-dsn';let lookups=0;
const context={PropertiesService:{getScriptProperties:()=>({getProperty:()=>{lookups++;return secret;}})},
  SpreadsheetApp:{openById:()=>({getSheetByName:()=>({getDataRange:()=>({getDisplayValues:()=>values.map(x=>x.slice())})})})},
  ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})}};
vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/Code.gs','utf8'),context);
const send=(pc,program='생산3공장 똑딱이')=>context.doPost({postData:{contents:JSON.stringify({pc_id:pc,program,action:'server_read_config'})}});
assert.equal(send('NOPC').ok,false);assert.equal(send('UNKNOWN').ok,false);
assert.equal(send('SCMPC','SCM 컨트롤타워').ok,false);assert.equal(lookups,0);
assert.equal(send('YESPC').result.server_read.dsn,secret);
secret='';assert.equal(send('YESPC').ok,false);
console.log('PASS: only Google-approved production3 identities receive common server settings');
