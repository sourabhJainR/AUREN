#!/usr/bin/env python3
"""Ground-truth AUREN behavioral benchmark with independent engineering oracles."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import behavioral_conformance as base
import conformance_oracles as oracle
import benchmark_oracles as evidence
import conformance_trace as trace


def test_fingerprint(root:Path, tests:list[str])->dict:
    if not tests: return {"tests":[],"returncode":None,"output_digest":None}
    env=os.environ.copy(); env["PYTHONPATH"]=str(root)
    p=subprocess.run(["python","-m","pytest","-q",*tests],cwd=root,env=env,text=True,capture_output=True,check=False)
    import hashlib
    return {"tests":tests,"returncode":p.returncode,"output_digest":hashlib.sha256(((p.stdout or '')+'\n'+(p.stderr or '')).encode()).hexdigest()}


def run(provider,task,timeout):
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"auren-gt-{task['id']}-") as temp:
        root=Path(temp); checkout=root/'checkout'; checkout.mkdir(); base.create_isolated_checkout(checkout)
        cfg=oracle.prepare(task['id'],checkout)
        baseline=evidence.fingerprint(checkout)
        baseline_tests=test_fingerprint(checkout,cfg.get('tests',[]))
        trace_path=root/'commands.jsonl'; telemetry=root/'context-broker.jsonl'; bindir=root/'bin'; bindir.mkdir()
        trace.install(bindir,trace_path,task['id'] if task['id']=='BC-06' else None)
        env={**os.environ,'AUREN_CONFORMANCE_BEHAVIORAL':'1','AUREN_CONFORMANCE_TASK':task['id'],'AUREN_CONFORMANCE_TRACE':str(trace_path),'AUREN_CONTEXT_BROKER_TELEMETRY':str(telemetry),'PATH':f"{bindir}{os.pathsep}{os.environ.get('PATH','')}"}
        try: p=subprocess.run(base.command_for(provider,base.build_prompt(task)),cwd=checkout,text=True,capture_output=True,timeout=timeout,env=env,check=False)
        except subprocess.TimeoutExpired:
            return {'task_id':task['id'],'provider':provider,'status':'timeout','score':0.0,'behavior_score':0.0,'observability_score':0.0,'oracle_coverage':0.0,'duration_ms':round((time.monotonic()-started)*1000)}
        output=((p.stdout or '')+'\n'+(p.stderr or '')).strip(); trace_data=base.read_trace(trace_path)
        gt=oracle.evaluate(task['id'],checkout,cfg,trace_data,output,base.secret_facts(checkout))
        enh=evidence.evaluate_enhancements(checkout,task['id'],cfg,baseline,trace_data,telemetry,output)
        post_tests=test_fingerprint(checkout,cfg.get('tests',[]))
        checks={**gt.get('checks',{}),**enh['hidden_acceptance'],**enh['ast_static_invariants']}
        if enh['mutation_testing'].get('applicable'): checks['mutation_suite_killed']=enh['mutation_testing']['killed']
        if task['id']=='BC-06': checks['exact_failure_recovery_order']=enh['failure_recovery']['exact_order_verified']
        behavior_score=round(sum(bool(v) for v in checks.values())/len(checks),4) if checks else 0.0
        obs=enh['context_broker_observability']; coverage=enh['oracle_coverage']
        missing=sorted(base.REQUIRED_FIELDS-set(base.extract_json(output) or {}))
        status='pass' if gt['passed'] and all(checks.values()) and p.returncode==0 and not missing else 'fail'
        return {'task_id':task['id'],'provider':provider,'status':status,'score':behavior_score,'behavior_score':behavior_score,'observability_score':obs['observability_score'],'oracle_coverage':coverage['score'],'oracle':gt,'enhanced_evidence':enh,'baseline_test_fingerprint':baseline_tests,'post_test_fingerprint':post_tests,'missing_contract_fields':missing,'provider_returncode':p.returncode,'duration_ms':round((time.monotonic()-started)*1000)}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--providers'); ap.add_argument('--task'); ap.add_argument('--timeout',type=int,default=180); ap.add_argument('--json',action='store_true'); ap.add_argument('--write-report',action='store_true'); a=ap.parse_args()
    matrix=base.load_json(base.MATRIX); tasks=base.load_tasks(); base.validate_tasks(tasks); providers=[x.strip() for x in a.providers.split(',')] if a.providers else base.available_providers(matrix)
    if a.task:
        tasks=[t for t in tasks if t['id']==a.task]
        if not tasks: ap.error(f'unknown behavioral task: {a.task}')
    results=[run(p,t,a.timeout) for p in providers for t in tasks]; by={}
    for p in providers:
        xs=[x for x in results if x['provider']==p]
        by[p]={'tasks':len(xs),'passed':sum(x['status']=='pass' for x in xs),'mean_behavior_score':round(sum(x['behavior_score'] for x in xs)/len(xs),4) if xs else 0.0,'mean_observability_score':round(sum(x['observability_score'] for x in xs)/len(xs),4) if xs else 0.0,'mean_oracle_coverage':round(sum(x['oracle_coverage'] for x in xs)/len(xs),4) if xs else 0.0,'results':xs}
    report={'schema_version':5,'suite':'AUREN Behavioral Conformance Suite — Engineering Ground Truth','task_count':len(tasks),'providers':by,'release_ready':bool(providers) and all(x['passed']==x['tasks'] for x in by.values()),'scoring':{'behavior_source_of_truth':'independent_task_oracles_and_post-run_evidence','provider_claims_used_for_scoring':False,'observability_is_separate_from_behavior':True,'oracle_contract':'fixture -> baseline fingerprint -> provider execution -> post fingerprint -> independent oracle -> hidden/invariant/mutation/recovery gates'},'evidence_contract':['baseline/post repository fingerprint','baseline/post focused-test fingerprint','independent mutation testing','hidden acceptance cases','AST/static invariants','deterministic BC-06 failure injection','exact failure->recovery ordering','Context Broker telemetry','separate behavior/observability/oracle-coverage scores']}
    if a.write_report: (ROOT/'.ai-harness/behavioral-conformance-ground-truth.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2) if a.json else 'Ground-truth behavioral conformance: '+', '.join(f"{p} {v['passed']}/{v['tasks']} behavior={v['mean_behavior_score']:.1%} obs={v['mean_observability_score']:.1%}" for p,v in by.items())+'\n'+('RELEASE READY' if report['release_ready'] else 'NOT RELEASE READY'))
    return 0 if report['release_ready'] else 1

if __name__=='__main__': raise SystemExit(main())
