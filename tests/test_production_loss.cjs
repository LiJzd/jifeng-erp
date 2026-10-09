const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const code = fs.readFileSync("jifeng_erp/public/js/jifeng_production_loss.js", "utf8");
const id = "e1d534d5-6bea-4b66-9836-981eec36c27e";
const newId = "f2d534d5-6bea-4b66-9836-981eec36c27e";
const source = {existing:false, source_version:"v1", loss_qty:5, stock_uom:"Nos", warehouse:"WIP"};
function fixture(answers, settings={}) {
  const calls=[], messages=[], routes=[], storage=new Map();
  let handlers, options, button, label, generated=0, reloaded=0, dialogs=0;
  const frm={doc:{name:"JC", modified:"v1", docstatus:1, total_completed_qty:0, pending_qty:0, process_loss_qty:5},
    is_dirty:()=>false, add_custom_button(value, callback){label=value;button=callback;},
    async reload_doc(){reloaded++;if(settings.reloadError) throw new Error("reload failed");button=null;handlers.refresh(frm);},
    refresh(){button=null;handlers.refresh(frm);}};
  Object.assign(frm.doc, settings.doc || {});
  if(settings.previous) storage.set("jf-loss:ordinary:JC",id);
  const window={cur_frm:frm};
  const frappe={session:{user:"ordinary"}, utils:{escape_html:String},
    ui:{form:{on(_name, value){handlers=value;}}, Dialog: class {
      constructor(value){dialogs++;options=value;} show(){} hide(){}
      get_primary_btn(){return {prop(){}};}
    }}, msgprint(value){messages.push(value);}, set_route(...value){routes.push(value);},
    async call(value){calls.push(value); const result=answers.shift();
      if(result instanceof Error) throw result; if(typeof result === "function") return {message:await result()};
      return {message:result};}};
  vm.runInNewContext(code,{frappe, window, crypto:{randomUUID:()=>{generated++;return settings.previous?newId:id;}}, localStorage:{getItem:key=>storage.get(key),
    setItem:(key,value)=>storage.set(key,value), removeItem:key=>storage.delete(key)}});
  handlers.refresh(frm);
  return {frm,window,calls,messages,routes,storage,click:()=>button(),
    state:()=>({label,generated,reloaded,dialogs,button}), primary:()=>options.primary_action({no_residual:1,reason:"报废",warehouse:"WIP"})};
}
(async()=>{
  let f=fixture([source,{stock_entry:"LOSS",docstatus:0}]);
  await f.click(); await f.primary(); await f.primary();
  assert.equal(f.calls.filter(row=>row.type==="POST").length,1,"double click sends one POST");
  assert.equal(f.calls[1].args.request_id,id);
  assert.equal(f.routes[0][2],"LOSS");

  f=fixture([source,new Error("write result unknown"),{existing:true,stock_entry:"LOSS",docstatus:0}]);
  await f.click(); await f.primary();
  assert.equal(f.calls.map(row=>row.type).join(","),"GET,POST,GET");
  assert.equal(f.routes[0][2],"LOSS");

  f=fixture([source,new Error("write unknown"),new Error("read unavailable")]);
  await f.click(); await f.primary(); await f.primary();
  assert.equal(f.calls.filter(row=>row.type==="POST").length,1);
  assert.equal([...f.storage.values()][0],id,"retain request for recovery");
  assert.equal(f.routes.length,0);

  let resolve; f=fixture([source,()=>new Promise(done=>{resolve=done;})]);
  await f.click(); const pending=f.primary(); f.window.cur_frm={doc:{name:"OTHER-CARD"}};
  resolve({stock_entry:"LOSS",docstatus:0}); await pending;
  assert.equal(f.routes.length,0,"late response must not move another Card");

  f=fixture([{existing:true,stock_entry:"LOSS",docstatus:2}]);
  await f.click();
  assert.equal(f.calls.filter(row=>row.type==="POST").length,0,"cancelled request is only read");
  assert.equal(f.storage.size,0);
  f=fixture([{existing:true,stock_entry:"LOSS",docstatus:2}], {previous:true,
    doc:{docstatus:2,total_completed_qty:3,process_loss_qty:0,pending_qty:2}});
  assert.equal(f.state().label,"回读损耗办理结果");
  await f.click();
  assert.equal(f.calls[0].args.only_previous,1);
  assert.equal(f.storage.size,0);
  assert.equal(f.state().generated,0);
  assert.equal(f.state().dialogs,0);
  assert.equal(f.state().reloaded,1);
  assert.equal(f.state().button,null,"ineligible current Card has no new creation action");
  assert.equal(f.calls.length,1,"manual history recovery only reads once");

  f=fixture([{existing:false,stock_entry:null,docstatus:null},source,{existing:false,stock_entry:"NEW",docstatus:0}], {previous:true});
  await f.click();
  assert.equal(f.storage.size,0,"proved absence releases old marker");
  assert.equal(f.state().generated,0,"recovery does not mint a request");
  assert.equal(f.state().dialogs,0);
  assert.equal(f.state().label,"全部损耗出库");
  await f.click();
  assert.equal(f.calls.filter(row=>row.type==="POST").length,0,"fresh source still requires a new confirmation");
  await f.primary();
  assert.equal(f.calls[2].args.request_id,newId);
  assert.equal(f.calls.filter(row=>row.type==="POST").length,1);

  f=fixture([new Error("read denied/lock busy")], {previous:true,doc:{process_loss_qty:0,total_completed_qty:5}});
  await f.click();
  assert.equal([...f.storage.values()][0],id,"failed read keeps exact old marker");
  assert.equal(f.state().generated,0);
  assert.equal(f.state().dialogs,0);
  f=fixture([{existing:false,stock_entry:null,docstatus:null}], {previous:true,reloadError:true});
  await f.click();
  assert.equal(f.storage.size,0);
  assert.ok(f.messages.some(message=>message.includes("旧标记已清除") && message.includes("刷新失败")));
  assert.ok(!f.messages.some(message=>message.includes("未能查明")));
  console.log("Production loss UI: duplicate, unknown result, recovery, late response and cancelled source passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
