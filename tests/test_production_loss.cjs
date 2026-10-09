const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const code = fs.readFileSync("jifeng_erp/public/js/jifeng_production_loss.js", "utf8");
const id = "e1d534d5-6bea-4b66-9836-981eec36c27e";
const source = {existing:false, source_version:"v1", loss_qty:5, stock_uom:"Nos", warehouse:"WIP"};
function fixture(answers) {
  const calls=[], messages=[], routes=[], storage=new Map();
  let handlers, options, button;
  const frm={doc:{name:"JC", modified:"v1", docstatus:1, total_completed_qty:0, pending_qty:0, process_loss_qty:5},
    is_dirty:()=>false, add_custom_button(_label, callback){button=callback;}};
  const window={cur_frm:frm};
  const frappe={session:{user:"ordinary"}, utils:{escape_html:String},
    ui:{form:{on(_name, value){handlers=value;}}, Dialog: class {
      constructor(value){options=value;} show(){} hide(){}
      get_primary_btn(){return {prop(){}};}
    }}, msgprint(value){messages.push(value);}, set_route(...value){routes.push(value);},
    async call(value){calls.push(value); const result=answers.shift();
      if(result instanceof Error) throw result; if(typeof result === "function") return {message:await result()};
      return {message:result};}};
  vm.runInNewContext(code,{frappe, window, crypto:{randomUUID:()=>id}, localStorage:{getItem:key=>storage.get(key),
    setItem:(key,value)=>storage.set(key,value), removeItem:key=>storage.delete(key)}});
  handlers.refresh(frm);
  return {frm,window,calls,messages,routes,storage,click:()=>button(), primary:()=>options.primary_action({no_residual:1,reason:"报废",warehouse:"WIP"})};
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
  console.log("Production loss UI: duplicate, unknown result, recovery, late response and cancelled source passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
