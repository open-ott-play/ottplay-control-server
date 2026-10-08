function (request, done) {
    "use strict";
    var w = window, media = w.__ottMedia, kiosk = w.__ottKiosk;
    var v = document.getElementById("video"), p = request.params || {};
    var deadline = Date.now() + request.timeoutMs;
    function active() {return Date.now() < deadline && w.__ottNativeCall && w.__ottNativeCall.id === request.id;}
    function finish(value) { done({ok: true, data: value}); }
    function fail(message) { done({ok: false, error: message}); }
    function selected() { return media && media.kioskSelection && media.kioskSelection(); }
    function hash(s) { var h = 2166136261; for (var i=0;i<s.length;i++) h = ((h ^ s.charCodeAt(i)) * 16777619) >>> 0; return h.toString(16); }
    function snapshot() {
        var q = selected(), state = kiosk && kiosk.snapshot();
        return {ready: !!(w.commandChannelsReady && media), provider: w.__ottActiveProviderDriver && w.__ottActiveProviderDriver.id,
            kiosk: state, touch: w.__ottNativeTouchGuardState || "unknown",
            video: v && {position: v.currentTime, duration: isFinite(v.duration) ? v.duration : null,
                paused: v.paused, ended: v.ended, ready: v.readyState, error: v.error ? v.error.code : 0,
                width: v.videoWidth, height: v.videoHeight, source: hash(v.currentSrc || "")},
            queue: q && {index:q.index, total:q.records.length, items:q.records.map(function(r){return {id:r.request && r.request.fid,title:r.title};})}};
    }
    if (request.action === "health") { finish(snapshot()); return; }
    if (request.action === "recover") {
        if (!v || !v.currentSrc) { fail("No current video"); return; }
        var pos=v.currentTime;
        if (v.error || v.ended) {
            var q=selected();
            if (!q || !media.restoreKiosk(q,function(){return active() && q.source===media.sourceId();})) {fail("No recoverable media selection");return;}
        } else { v.pause(); v.play(); }
        finish({dispatched:true,position:pos}); return;
    }
    if (request.action === "playback") {
        if (!v || !v.currentSrc) {fail("No current video");return;}
        if (p.operation === "pause" && kiosk && kiosk.locked()) {fail("Disable kiosk before pausing playback");return;}
        if (p.operation === "pause") v.pause();
        else if (p.operation === "resume") v.play();
        else if (p.operation === "seek" && typeof p.position === "number" && isFinite(p.position) && p.position>=0 && isFinite(v.duration) && p.position<=v.duration) v.currentTime=p.position;
        else {fail("Invalid playback operation");return;}
        finish({dispatched:true,operation:p.operation,position:v.currentTime});return;
    }
    if (request.action === "vportal_queue") {
        if (!media || !kiosk || !w.__ottActiveProviderDriver || w.__ottActiveProviderDriver.id!=="vportal") {fail("Select and configure VPortal first");return;}
        if (p.operation === "status") {finish(snapshot().queue || {index:0,total:0,items:[]});return;}
        var old=selected(), source=media.sourceId(), oldPolicy=w.stbGetItem("__ottKioskV1"), locked=kiosk.locked(), strict=!!(kiosk.strict && kiosk.strict()), revision={};
        w.__ottNativeQueueRevision=revision;
        function contextCurrent(){return w.__ottNativeQueueRevision===revision && media.sourceId()===source;}
        function current(){return active() && contextCurrent();}
        if (p.operation === "stop") {
            kiosk.request({mode:"off"},function(r){if(r.status!=="ok"){fail("Kiosk release failed");return;}media.cancelAuto();w.stbStop();finish({stopped:true});});return;
        }
        if (p.operation !== "play") {
            if (!old) {fail("VPortal queue is empty");return;}
            var index=old.index;
            if (p.operation === "next") index=(index+1)%old.records.length;
            else if (p.operation === "previous") index=(index+old.records.length-1)%old.records.length;
            else if (p.operation === "restart") index=0;
            else {fail("Invalid queue operation");return;}
            old.index=index;old.position=0;
            if(!media.restoreKiosk(old,current)){fail("Queue change rejected");return;}
            finish({dispatched:true,index:index,total:old.records.length,title:old.records[index].title});return;
        }
        if (p.loop !== true || !Array.isArray(p.ids) || !p.ids.length || p.ids.length>100) {fail("This VPortal adapter requires a looping queue of 1–100 IDs");return;}
        var config, portal, route, jq=w.jQuery||w.$;
        try {config=JSON.parse(localStorage.vportalprofiles);portal=w.parseVPortalLink(config.portals[config.active].link);
            var hosted=w.__OTTPLAY_HOSTED__, routes=hosted && hosted.version===1 && hosted.vportal && hosted.vportal.routes;
            if(!Array.isArray(routes))throw new Error();
            routes.forEach(function(r){if(r && r.upstream===portal.url){if(route || typeof r.path!=="string" || !/^\/vportal\/[a-z0-9]+(?:-[a-z0-9]+)*$/.test(r.path))throw new Error();route=r.path;}});
            if(!route||!jq)throw new Error();}
        catch(e){fail("VPortal configuration is unavailable");return;}
        var rows=[], cursor=0, unlocked=false, complete=false;
        var cancelStart=null;
        var timer=setTimeout(function(){abort("VPortal queue preparation timed out");},Math.min(25000,request.timeoutMs));
        function abort(message) {
            if(complete)return;complete=true;clearTimeout(timer);
            if(cancelStart)cancelStart();
            if(unlocked&&contextCurrent()) {try{w.stbSetItem("__ottKioskV1",oldPolicy);location.reload();}catch(e){}}
            fail(message);
        }
        function begin() {
            if(!current()){abort("VPortal context changed");return;}
            cancelStart=media.playQueue(rows,"Selected VPortal queue",function(){return current()&&!complete;},function(){
                if(!current()||complete){abort("VPortal context changed");return;}
                function accepted(){complete=true;clearTimeout(timer);finish({dispatched:true,loop:true,total:rows.length,items:rows.map(function(r){return {id:r.request.fid,title:r.title};})});}
                kiosk.request({mode:"on",strict:strict},function(r){if(r.status!=="ok"){abort("Kiosk save failed");return;}unlocked=false;accepted();});
            });
        }
        function load() {
            if(!current()||complete){abort("VPortal context changed");return;}
            if(cursor===p.ids.length) {
                if(locked)kiosk.request({mode:"off"},function(r){if(!current()){abort("VPortal context changed");return;}if(r.status!=="ok"){abort("Kiosk release failed");return;}unlocked=true;begin();});else begin();return;
            }
            var id=p.ids[cursor++];
            if(typeof id!=="number" || id<1 || id%1!==0){abort("Invalid VPortal ID");return;}
            jq.ajax({url:route,type:"POST",contentType:"application/json; charset=UTF-8",dataType:"json",timeout:10000,
                data:JSON.stringify({app:"ott-play",key:portal.key,cmd:"flick",fid:id}),
                success:function(r){
                    if(!current()||complete){abort("VPortal context changed");return;}
                    if(!r||r.type!=="stream"||!r.title){abort("VPortal item unavailable");return;}
                    if(w.sPSchannels&&w.parentPIN!=="*"&&!w.parentAccess&&(Number(r.adult)===1||Number(r.agelimit)>=18)){abort("Unlock parental access first");return;}
                    rows.push({title:String(r.title),request:{cmd:"flick",fid:id},vportalSource:source,adult:r.adult});load();
                },error:function(){abort("VPortal item request failed");}});
        }
        load();return;
    }
    fail("Unsupported native player operation");
}
