import json,sys,hashlib,statistics,subprocess
from pathlib import Path
ROOT=Path('E:/seat_protect_collab_candidate');sys.path.insert(0,str(ROOT))
from native.run_native_formal24 import assignment_map
from src import allocation_evaluator as ev
cgroot=Path('E:/seat_protect_worktrees/cg-d4');b=cgroot/'reports/audit-2026-09-23/benchmark';out=ROOT/'outputs/benchmarks/formal24_cpp5_vs_cg_2026-09-28';out.mkdir(parents=True,exist_ok=True)
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def stats(a):return {'n':len(a),'mean':statistics.mean(a),'median':statistics.median(a),'worst':max(a),'le1':sum(v<=1 for v in a),'le2':sum(v<=2 for v in a),'le5':sum(v<=5 for v in a)} if a else {'n':0}
sdir=out/'current_main_run';cpp=read(sdir/'summary.json');cfg=read(ROOT/'configs/config_native_cpp5_gap2.json');rows=[];sources={}
for direction in ['forward','reverse']:
 p150=b/f'formal24_{direction}_timing.json';p600=b/f'formal24_{direction}_600s_new.json'
 a={r['instance']:r for r in read(p150)};z={r['instance']:r for r in read(p600)}
 sources[str(p150)]=sha(p150);sources[str(p600)]=sha(p600)
 for c in cpp['cases']:
  d,name=c['case_id'].split(':')
  if d!=direction:continue
  ip=ROOT/f'data/generator_v3_2/d3_official_24cases_2026-09-07/{d}/{name}_groups.json';raw=read(ip);cginput=cgroot/ip.relative_to(ROOT)
  assert raw==read(cginput)==read(ROOT/f'data/generator_v3_2/d3_official_24cases_2026-09-07/{d}/{ip.name}')
  cgcase=a[name+'_groups'];g=raw['groups'];assert cgcase['pax']==sum(len(x['psrs']) for x in g) and cgcase['groups']==len(g) and cgcase['max_group']==max(len(x['psrs']) for x in g)
  maps=cfg['input_contract']['seatmaps_by_direction'][d];oldp=(ROOT/'configs'/maps['old']).resolve();newp=(ROOT/'configs'/maps['new']).resolve()
  old=read(oldp)['seats'];new=read(newp)['seats'];allocation=read(sdir/'allocations'/(c['case_id'].replace(':','_')+'.json'));assignment=assignment_map(allocation)
  h,u,_=ev.count_hard_constraint_violations(new,g,assignment,cfg);s,_=ev.calculate_soft_score(new,old,g,assignment,cfg['weights'],cfg)
  assert h==u==0 and abs(s-c['external_score'])<1e-7
  r={'case_id':c['case_id'],'cpp_score':s,'cpp_wall':c['process_wall_seconds'],'input_sha256':sha(ip),'cpp_allocation_reaudit_pass':True,'cg150':cgcase['new'],'cg600':z.get(name+'_groups',{}).get('new')}
  for key in ['cg150','cg600']:
   m=r[key]
   if m is not None:assert m['violations']==m['unassigned']==0 and abs(sum(m[x] for x in ['s','v','p','c','b'])-m['score'])<1e-6
  selected=r['cg600'] if r['cg600'] is not None else r['cg150'];r['selected_budget']=600 if r['cg600'] is not None else 150;r['selected_score']=selected['score']
  for label,m in [('150',r['cg150']),('selected',selected)]:
   certified=m.get('U_cert') and m.get('U') is not None;ref=m['U'] if certified else m['score'];r['reference_type_'+label]='reported_gap_LP' if certified else 'gap_CG_integer';r['reference_'+label]=ref;r['gap_'+label]=100*(ref-s)/max(1,abs(ref));r['delta_'+label]=s-m['score']
  rows.append(r)
summary={'cpp_summary_sha256':sha(sdir/'summary.json'),'cg_sources':sources,'evidence_limitation':'CG aggregate artifacts do not contain per-run input/config hashes or assignments. LP certification reported, not independently recertified. Inputs in both repositories semantically equal; evaluator and objective sections equal; C++ allocations independently reaudited. Timing across historical environments is not a controlled speed benchmark.','cases':rows}
for label in ['150','selected']:
 summary[label]={'cpp_better':sum(r['delta_'+label]>1e-6 for r in rows),'tie':sum(abs(r['delta_'+label])<=1e-6 for r in rows),'cpp_worse':sum(r['delta_'+label]<-1e-6 for r in rows),'by_reference':{kind:stats([r['gap_'+label] for r in rows if r['reference_type_'+label]==kind]) for kind in ['reported_gap_LP','gap_CG_integer']}}
(out/'comparison.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
lines=['# Formal24: current C++ 5s vs archived repaired column generation','',summary['evidence_limitation'],'','600s column uses the 150s result when no additional run exists; no best-of selection.','', '| Case | C++ 5s | CG 150s | CG 600s | Ref type | Ref | C++ gap % | C++ wall s |','|---|---:|---:|---:|---|---:|---:|---:|']
for r in rows:lines.append(f"| {r['case_id']} | {r['cpp_score']:.4f} | {r['cg150']['score']:.4f} | "+(f"{r['cg600']['score']:.4f}" if r['cg600'] else 'not run')+f" | {r['reference_type_selected']} | {r['reference_selected']:.4f} | {r['gap_selected']:.3f} | {r['cpp_wall']:.3f} |")
lines+=['','```json',json.dumps({k:summary[k] for k in ['150','selected']},indent=2),'```']
(out/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({k:summary[k] for k in ['150','selected']},indent=2))
for r in rows:print(r['case_id'],round(r['cpp_score'],3),round(r['cg150']['score'],3),round(r['selected_score'],3),r['reference_type_selected'],round(r['gap_selected'],3))
