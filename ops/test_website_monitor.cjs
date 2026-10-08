const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname, 'WebsiteQueueMonitor.gs'),'utf8');
const props=new Map(), sent=[], triggers=[];let healthy=false, throwSend=false;
const properties={getProperty:key=>props.get(key),setProperty:(key,value)=>props.set(key,value),deleteProperty:key=>props.delete(key)};
const context={console,LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
PropertiesService:{getScriptProperties:()=>properties}, UrlFetchApp:{fetch:()=>({getResponseCode:()=>healthy?200:503,getContentText:()=>JSON.stringify({ok:healthy})})},
MailApp:{sendEmail:(...args)=>{if(throwSend)throw Error('ambiguous');sent.push(args);}},
ScriptApp:{getProjectTriggers:()=>triggers,newTrigger:handler=>({timeBased:()=>({everyMinutes:minutes=>({create:()=>triggers.push({getHandlerFunction:()=>handler,minutes})})})})}};
vm.createContext(context);vm.runInContext(source,context);
context.enableWebsiteQueueMonitor();context.enableWebsiteQueueMonitor();assert.equal(triggers.length,1);assert.equal(triggers[0].minutes,5);
context.checkWebsiteQueueHealth();assert.equal(sent.length,0);context.checkWebsiteQueueHealth();assert.equal(sent.length,1);
context.checkWebsiteQueueHealth();assert.equal(sent.length,1);healthy=true;context.checkWebsiteQueueHealth();assert.equal(sent.length,2);
context.checkWebsiteQueueHealth();assert.equal(sent.length,2);healthy=false;context.checkWebsiteQueueHealth();throwSend=true;
assert.throws(()=>context.checkWebsiteQueueHealth(),/ambiguous/);throwSend=false;context.checkWebsiteQueueHealth();assert.equal(sent.length,2);
console.log('Independent monitor checks passed: one trigger, transient-failure tolerance, incident/recovery deduplication, ambiguous email protection.');
