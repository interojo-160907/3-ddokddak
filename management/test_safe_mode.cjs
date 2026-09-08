const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
let mode = '안전모드', calls = 0;
const values = new Map();
const context = {
  text_: v => String(v || '').trim(),
  LockService:{getScriptLock:()=>({waitLock(){},releaseLock(){}})},
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>values.get(k),setProperty:(k,v)=>values.set(k,v),deleteProperty:k=>values.delete(k)})},
  UrlFetchApp:{fetch(url){
    calls++;
    const data = url.includes('/contents/') ? [
      {type:'file',name:'260907_오후.xlsx',sha:'b'},
      {type:'file',name:'260908_오전.xlsx',sha:'a'},
      {type:'file',name:'사용안내.md',sha:'c'}
    ] : url.includes('/commits/') ? {sha:'1'.repeat(40)} : {default_branch:'main'};
    return {getResponseCode:()=>200,getContentText:()=>JSON.stringify(data)};
  }}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/SafeMode.gs','utf8'),context);
const spreadsheet={getSheetByName:()=>({getDataRange:()=>({getDisplayValues:()=>[['A','B','C'],['다른 프로그램','허가','자동모드'],['생산3공장 똑딱이','허가',mode]]})})};
const first=context.safeModeControl_(spreadsheet,'생산3공장 똑딱이');
assert.equal(first.safe_asset.name,'260908_오전.xlsx');
assert.equal(calls,3);
assert.equal(context.safeModeControl_(spreadsheet,'생산3공장 똑딱이').safe_asset.selection_id,first.safe_asset.selection_id);
assert.equal(calls,3); // Other PCs and repeated polls reuse the pinned commit.
mode='자동모드';assert.equal(context.safeModeControl_(spreadsheet,'생산3공장 똑딱이').mode,mode);
assert.equal(values.size,0);
mode='안전모드';context.safeModeControl_(spreadsheet,'생산3공장 똑딱이');assert.equal(calls,6);
mode='자동모르';assert.throws(()=>context.safeModeControl_(spreadsheet,'생산3공장 똑딱이'));
console.log('PASS: global program row, latest filename, shared immutable selection, auto reset, invalid mode');
