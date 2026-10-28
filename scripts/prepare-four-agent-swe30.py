import json,random,hashlib,collections,sys
from pathlib import Path
from datasets import Dataset
ROOT=Path(__file__).resolve().parents[1]
REV='c104f840cc67f8b6eec6f759ebc8b2693d585d4a'
arrow=Path.home()/f'.cache/huggingface/datasets/princeton-nlp___swe-bench_verified/default/0.0.0/{REV}/swe-bench_verified-test.arrow'
records=list(Dataset.from_file(str(arrow)));byid={r['instance_id']:r for r in records}
sources=collections.defaultdict(set)
# Conservative: reserve every task previously materialized or frozen, including preparations without model calls.
for root in [ROOT/'datasets',ROOT/'artifacts',ROOT/'.console/inputs']:
 for f in root.glob('**/task.toml'):
  if f.parent.name in byid:sources[f.parent.name].add(str(f.relative_to(ROOT)))
 for f in root.glob('**/selection.json'):
  d=json.loads(f.read_text())
  for key in ['instance_ids','excluded']:
   for t in d.get(key,[]):
    if t in byid:sources[t].add(str(f.relative_to(ROOT)))
for f in (ROOT/'experiments').glob('**/*.json'):
 try:d=json.loads(f.read_text())
 except (ValueError,OSError):continue
 for t in d.get('benchmark',{}).get('tasks',[]):
  if Path(t).name in byid:sources[Path(t).name].add(str(f.relative_to(ROOT)))
remaining=[r for r in records if r['instance_id'] not in sources]
groups=collections.defaultdict(list)
for r in remaining:groups[r['repo']].append(r['instance_id'])
# Proportional largest-remainder allocation, fixed seed and sorted strata.
n=30;total=len(remaining);quota={repo:len(ids)*n//total for repo,ids in groups.items()}
for repo in sorted(groups,key=lambda r:(-(len(groups[r])*n%total),r))[:n-sum(quota.values())]:quota[repo]+=1
rng=random.Random(2026101702);ids=[]
for repo in sorted(groups):ids+=rng.sample(sorted(groups[repo]),quota[repo])
rng.shuffle(ids)
assert len(set(ids))==30 and not set(ids)&sources.keys()
target=ROOT/'datasets/swe-four-agent-30-20261017';target.mkdir(exist_ok=False)
selection={'dataset':'princeton-nlp/SWE-bench_Verified','revision':REV,'split':'test','seed':2026101702,'sampling':'proportional repo strata; largest remainder allocation; sorted strata and random.Random(seed).sample then shuffle','excluded':sorted(sources),'exclusion_sources':{k:sorted(v) for k,v in sorted(sources.items())},'available_count':total,'repo_allocation':quota,'instance_ids':ids,'source_arrow_sha256':hashlib.sha256(arrow.read_bytes()).hexdigest(),'note':'排除本地所有历史物化/冻结/配置题，含未实际调用模型的准备题；不声称模型预训练未见过。'}
(target/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n')
(target/'records.json').write_text(json.dumps([byid[i] for i in ids],ensure_ascii=False,indent=2)+'\n')
sys.path.insert(0,str(ROOT/'.cache/harbor-source/adapters/swebench/src'))
from swebench_adapter import adapter
adapter.load_dataset=lambda *a,**k:{'test':[byid[i] for i in ids]}
c=adapter.SWEBenchAdapter(target,task_ids=ids,max_timeout_sec=1800)
for i in ids:c.generate_task(i,i)
print(json.dumps({'directory':str(target),'excluded':len(sources),'available':total,'allocation':quota,'ids':ids},indent=2))
