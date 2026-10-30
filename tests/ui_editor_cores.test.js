import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
class Element {
  constructor() {this.children=[];this.listeners={};this.dataset={};this.style={setProperty(){}};this.value='';}
  set innerHTML(value) {this.children=[];}
  appendChild(child) {this.children.push(child);return child;}
  replaceChildren() {this.children=[];}
  addEventListener(type,handler) {this.listeners[type]=handler;}
  setAttribute() {}
}
function editor() {
  const nodes=new Map();const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const sent=[];
  const context=vm.createContext({currentCore:'pru0',availableCores:['pru0','pru1','rtu0','rtu1'],
    document:{getElementById:get,createElement:()=>new Element()},
    PruCores:{ORDER:['pru0','pru1','rtu0','rtu1']},asmSource:get('asm-source'),btnLoad:get('btn-load'),btnSaveAsm:get('btn-save-asm'),
    confirm:()=>true,sendAction:action=>sent.push(action),stopRun(){},stopSim(){},clearErrors(){},
    mcFreshRegs:()=>[],sourceList:get('source-list'),coreStates:{},mcLastSourceKey:{},console,
    fetch:async()=>({ok:true,text:async()=>''}),
  });
  const run=code=>vm.runInContext(code,context);
  run(app.slice(app.indexOf('// ---- Editor tab state'),app.indexOf('// ---- Signal graph state')));
  run(app.slice(app.indexOf('// ---- Tab management'),app.indexOf('// ---- Build static UI elements')));
  run(app.slice(app.indexOf('btnLoad.addEventListener'),app.indexOf('// ---- Signal graph controls')));
  return {context,get,run,sent};
}

test('#62: closing a file on one core leaves the same file and draft on another core',()=>{
  const e=editor();
  e.run("openFileAsTab('shared.asm','ldi r1, 1'); selectEditorCore('pru1'); openFileAsTab('shared.asm','ldi r1, 2');");
  e.get('asm-source').value='ldi r1, 99';
  e.run("selectEditorCore('pru0'); closeTab(0); selectEditorCore('pru1');");
  assert.equal(e.get('asm-source').value,'ldi r1, 99');
  e.run('closeTab(0)');
  assert.equal(e.get('asm-source').value,'');
  assert.equal(e.sent.length,0,'switching or closing editor files never unloads or runs firmware');
});

test('the editor selector loads an unused core without changing execution membership',async()=>{
  const e=editor();
  e.run("syncEditorCoreSelect();");
  const select=e.get('assembly-core-target');select.value='rtu1';
  select.listeners.change({target:select});
  e.run("openFileAsTab('reader.asm','ldi r5, 7');");
  await e.get('btn-load').listeners.click();
  assert.equal(e.sent.at(-1).core,'rtu1');
  assert.equal(e.sent.at(-1).source,'ldi r5, 7');
  assert.equal(e.context.currentCore,'pru0','loading target does not move the inspector');
});

test('Load keeps the selected core and code when autosave waits and the user switches cores',async()=>{
  const e=editor();let release;
  e.context.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:true});});
  e.run("openFileAsTab('first.asm','ldi r0, 1');");
  e.get('asm-source').value='ldi r0, 11';
  // Input event marks the file dirty just as typing does in the dashboard.
  e.run("editor.tabs[0].dirty=true;");
  const loading=e.get('btn-load').listeners.click();
  e.run("selectEditorCore('pru1'); openFileAsTab('second.asm','ldi r0, 22');");
  release();await loading;
  assert.equal(e.sent.at(-1).core,'pru0');
  assert.equal(e.sent.at(-1).source,'ldi r0, 11');
  assert.equal(e.get('asm-source').value,'ldi r0, 22');
});

test('a delayed file open goes to its original core without replacing the selected core draft',()=>{
  const e=editor();
  e.run("selectEditorCore('pru1'); openFileAsTab('pru1.asm','ldi r1, 2'); openFileAsTab('pru0.asm','ldi r1, 1','pru0');");
  assert.equal(e.get('asm-source').value,'ldi r1, 2');
  e.run("selectEditorCore('pru0');");
  assert.equal(e.get('asm-source').value,'ldi r1, 1');
});

test('initial file fetch cannot move its result into another core or overwrite a draft',async()=>{
  const e=editor();let release;
  e.context.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:true,text:async()=> 'ldi r0, 42'});});
  const opening=e.run('initEditorTabs()');
  e.run("selectEditorCore('pru1');");e.get('asm-source').value='ldi r1, 77';
  release();await opening;
  assert.equal(e.get('asm-source').value,'ldi r1, 77');
  e.run("selectEditorCore('pru0');");assert.equal(e.get('asm-source').value,'ldi r0, 42');
  e.run("selectEditorCore('pru1');");assert.equal(e.get('asm-source').value,'ldi r1, 77');
});

test('each core loads its own project in manifest order after another core closes its files',async()=>{
  const e=editor();
  const a=app.indexOf('async function openProject('),b=app.indexOf('btnOpenProject.addEventListener',a);
  e.run(app.slice(a,b));
  e.context.fetch=async path=>path.endsWith('project.json')
    ? {ok:true,json:async()=>({files:['b.asm','a.asm']})}
    : {ok:true,text:async()=>path.includes('/first/')?'ldi r0, 1':'ldi r0, 2'};
  await e.context.openProject('first',['a.asm','b.asm']);
  e.run("selectEditorCore('pru1');");
  await e.context.openProject('second',['a.asm','b.asm']);
  e.run("selectEditorCore('pru0'); closeTab(1); closeTab(0); selectEditorCore('pru1');");
  await e.get('btn-load').listeners.click();
  assert.equal(e.sent.at(-1).core,'pru1');
  assert.equal(e.sent.at(-1).filename,'second/b.asm');
  assert.equal(e.sent.at(-1).source,'ldi r0, 2\nldi r0, 2');
});
