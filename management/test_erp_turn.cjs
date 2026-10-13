const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let now=1000000;const props=new Map();
const ctx={Date:{now:()=>now},text_:v=>String(v||'').trim(),allowed_:(r,i)=>r[i]==='Y',
 LockService:{getScriptLock:()=>({waitLock(){},releaseLock(){}})},
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k),setProperty:(k,v)=>props.set(k,v)})}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(__dirname+'/ErpTurn.gs','utf8'),ctx);
const users={pc:0,program:1,name:2,use:3,order:4,rows:[
 ['PC1','production3','일반1','Y','1'],['PC2','production3','일반2','Y','2'],
 ['ADMIN','production3','이상호','Y','99'],['OFF','production3','꺼진 PC','Y','1'],
 ['OTHER','other','이상호','Y','1']]};
const call=(pc,t,action='erp_acquire',failed=false)=>ctx.erpTurn_(pc,{program:'production3',request_token:t.repeat(32),action,failed},users);
const reset=()=>{props.clear();now+=1000000;};
call('PC1','a');call('PC2','b');call('ADMIN','c');now+=5001;
assert.equal(call('PC1','a').granted,false);assert.equal(call('ADMIN','c').granted,true);
assert.equal(call('PC2','b').active_user,'이상호');
call('ADMIN','c','erp_release');assert.equal(call('PC1','a').granted,true);
call('ADMIN','d');assert.equal(call('ADMIN','d').granted,false);
call('PC1','a','erp_release');assert.equal(call('ADMIN','d').granted,true);
call('PC2','b','erp_release');assert.equal(call('PC1','a').granted,false);
now+=500000;assert.equal(call('ADMIN','d','erp_renew').granted,true);
call('ADMIN','d','erp_release',true);assert.equal(call('PC1','a').status,'cooldown');
reset();call('PC1','a');call('PC2','b');now+=180001;
call('PC2','b');now+=5001;assert.equal(call('PC2','b').granted,true);
call('PC2','b','erp_release');call('PC1','a');now+=5001;assert.equal(call('PC1','a').granted,true);
reset();call('PC1','a');now+=5001;call('PC1','a');call('PC2','b');now+=600001;
assert.equal(call('PC2','b').status,'cooldown');now+=120001;
assert.equal(call('PC2','b').granted,true);
call('PC1','a','erp_release');assert.equal(call('PC2','b','erp_renew').granted,true);
assert.throws(()=>call('OTHER','e'),/권한/);
console.log('PASS: admin priority, H order, offline and late PCs, renew, crash drain, failure cooldown, stale release, permissions');
