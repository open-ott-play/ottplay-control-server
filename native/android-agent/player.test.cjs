const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const acorn = require('acorn');
const source = fs.readFileSync(__dirname + '/player.js', 'utf8');
function checkKitkatSyntax(source) {
  const ast=acorn.parse('('+source+')',{ecmaVersion:5});
  function visit(node,parent,grandparent) {
    if(!node || typeof node!=='object')return;
    if(node.type==='FunctionDeclaration') {
      // Acorn accepts these as an extension even in ES5 strict mode; Chromium 30 does not.
      assert.ok(parent.type==='Program' || (parent.type==='BlockStatement' &&
        grandparent && /^(FunctionDeclaration|FunctionExpression)$/.test(grandparent.type) && grandparent.body===parent),
        'KitKat rejects function declarations inside statement blocks');
    }
    for(const value of Object.values(node)) {
      if(Array.isArray(value))for(const child of value)visit(child,node,parent);
      else if(value && typeof value==='object')visit(value,node,parent);
    }
  }
  visit(ast,null,null);
}
test('embedded adapter uses strict ES5 accepted by Chromium 30',()=>{
  checkKitkatSyntax(source);
  assert.throws(()=>checkKitkatSyntax('function(){"use strict";if(true){function nested(){}}}'),/KitKat rejects/);
});
function fixture() {
  const calls=[], pending=[], timers=new Map(); let timer=0, reply, sequence=0;
  const video={currentSrc:'private stream',currentTime:12,duration:100,paused:false,pause(){calls.push('pause')},play(){calls.push('resume')}};
  let selected={source:'vportal:test',index:1,records:[{request:{fid:47677},title:'First'},{request:{fid:44819},title:'Second'},{request:{fid:47674},title:'Third'}]};
  const w={__ottNativeCall:{id:'test'}, __ottActiveProviderDriver:{id:'vportal'},
    __OTTPLAY_HOSTED__:{version:1,vportal:{routes:[{upstream:'https://provider.test/api',path:'/vportal/local'}]}},
    parseVPortalLink:()=>({url:'https://provider.test/api',key:'private key'}),
    stbGetItem:()=>'{"old":"policy"}',stbSetItem:(key,val)=>calls.push(['restore',key,val]),stbStop:()=>calls.push('stop'),
    jQuery:{ajax(opts){pending.push(opts)}},
    __ottKiosk:{locked:()=>true,strict:()=>true,snapshot:()=>({enabled:true,state:'locked'}),request(p,cb){calls.push(['kiosk',p]);cb({status:'ok'})}},
    __ottMedia:{sourceId:()=> 'vportal:test',kioskSelection:()=>selected,cancelAuto(){calls.push('cancel')},
      restoreKiosk(s,guard){if(guard()){calls.push(['restoreQueue',s]);return true}return false},
      playQueue(rows,title,guard,cb){if(guard()){calls.push(['play',rows]);cb()}return ()=>calls.push('cancelStart')}}};
  const context={window:w,document:{getElementById:()=>video},localStorage:{vportalprofiles:'{"active":0,"portals":[{"link":"x"}]}'},
    location:{reload(){calls.push('reload')}},setTimeout(fn){timers.set(++timer,fn);return timer},clearTimeout(id){timers.delete(id)},Date,console};
  const fn=vm.runInNewContext('('+source+')', context);
  return {w,calls,pending,timers,run(action,params){const id='call-'+(++sequence);w.__ottNativeCall={id};fn({action,params,id,timeoutMs:20000},r=>reply=r);return reply},reply:()=>reply};
}
test('queue preflights all items, preserves order, and restores strict kiosk',()=>{
 const f=fixture();f.run('vportal_queue',{operation:'play',ids:[47677,44819,47674],loop:true});
 assert.equal(f.calls.length,0);
 for(let i=0;i<3;i++){assert.equal(f.pending[i].url,'/vportal/local');f.pending[i].success({type:'stream',title:'Film '+i});if(i<2)assert.equal(f.calls.length,0)}
 assert.equal(f.reply().ok,true);assert.deepEqual(JSON.parse(JSON.stringify(f.reply().data.items.map(x=>x.id))),[47677,44819,47674]);
 assert.equal(f.calls.at(-1)[1].strict,true);
});
test('metadata failure keeps current kiosk untouched',()=>{
 const f=fixture();f.run('vportal_queue',{operation:'play',ids:[1],loop:true});f.pending[0].error();assert.equal(f.reply().ok,false);assert.equal(f.calls.length,0);
});
test('timeout fences delayed metadata callback',()=>{
 const f=fixture();f.run('vportal_queue',{operation:'play',ids:[1],loop:true});[...f.timers.values()][0]();f.pending[0].success({type:'stream',title:'Late'});assert.equal(f.reply().ok,false);assert.equal(f.calls.length,0);
});
test('provider change prevents old queue delivery',()=>{
 const f=fixture();f.run('vportal_queue',{operation:'play',ids:[1],loop:true});f.w.__ottMedia.sourceId=()=> 'changed';f.pending[0].success({type:'stream',title:'Late'});assert.equal(f.reply().ok,false);assert.equal(f.calls.length,0);
});
test('failed kiosk save rolls back persisted policy after starting playback',()=>{
 const f=fixture();f.w.__ottKiosk.request=(p,cb)=>{f.calls.push(['kiosk',p]);cb({status:p.mode==='on'?'rejected':'ok'})};
 f.run('vportal_queue',{operation:'play',ids:[1],loop:true});f.pending[0].success({type:'stream',title:'First'});
 assert.equal(f.reply().ok,false);assert.equal(f.calls.at(-1),'reload');assert.equal(f.calls.at(-2)[0],'restore');
});
test('restart returns to first exact item without unlocking kiosk',()=>{
 const f=fixture();const r=f.run('vportal_queue',{operation:'restart'});assert.equal(r.ok,true);assert.equal(f.calls[0][1].index,0);assert.equal(f.calls[0][1].position,0);assert.equal(f.calls.length,1);
});
test('native recover cycles surface without seeking or unlocking',()=>{
 const f=fixture();assert.equal(f.run('recover',{}).ok,true);assert.deepEqual(f.calls,['pause','resume']);
});
test('pause cannot falsely succeed against the legacy kiosk watchdog',()=>{
 const f=fixture();assert.equal(f.run('playback',{operation:'pause'}).ok,false);assert.equal(f.calls.length,0);
});

test('health polling does not revoke an asynchronous queue restore lease',()=>{
 const f=fixture();let guard;
 f.w.__ottMedia.restoreKiosk=(selection,valid)=>{guard=valid;return true};
 f.run('vportal_queue',{operation:'restart'});assert.equal(guard(),true);
 f.run('health',{});assert.equal(guard(),true);
 f.run('playback',{operation:'resume'});assert.equal(guard(),false);
});

test('frequent health probes omit episode titles while queue status retains them',()=>{
 const f=fixture();const health=f.run('health',{});
 assert.equal(health.data.queue.total,3);assert.equal(health.data.queue.items,undefined);
 const queue=f.run('vportal_queue',{operation:'status'});
 assert.equal(queue.data.total,3);assert.equal(queue.data.items.length,3);
 assert.equal(queue.data.items[1].id,44819);
});

test('native identity uses the same web runtime, generation and main handle',()=>{
 const f=fixture();
 f.w.__ottRemoteInspect={snapshot:()=>({version:1,runtime:'web-1',consistent:true,media:{generation:7,kind:'vod',lanes:[{lane:'main',handleId:12}]}})};
 const id=f.run('health',{}).data.identity;
 assert.equal(id.available,true);assert.equal(id.web_runtime,'web-1');assert.equal(id.generation,7);assert.equal(id.handle_id,12);
 assert.equal(f.calls.length,0);
 f.w.__ottRemoteInspect.snapshot=()=>({version:1,runtime:'web-2',consistent:false});
 const next=f.run('health',{}).data.identity;
 assert.equal(next.available,false);assert.equal(next.web_runtime,'web-2');assert.equal(next.handle_id,null);
});
test('old or failing web builds never invent shared media identity',()=>{
 const f=fixture();assert.equal(f.run('health',{}).data.identity.available,false);
 f.w.__ottRemoteInspect={snapshot(){throw new Error('private URL')}};
 assert.equal(f.run('health',{}).data.identity.available,false);
 assert.ok(!JSON.stringify(f.reply()).includes('private URL'));
});

test('legacy decoder counters never claim presented frames or audible sound',()=>{
 const f=fixture();const d=f.run('health',{}).data.decoder;
 assert.equal(d.decoded_frames,null);assert.equal(d.presented_frames,null);assert.equal(d.audible_verified,false);
});
