"""Measured paired optimizer campaign aggregation, without promotion authority."""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Mapping, Sequence
from .quality_optimizer_contract import verify_paired_run_identity
METRICS=("input_tokens","output_tokens","latency_ms","cost_usd")
@dataclass(frozen=True)
class PairedTrial:
 trial_id:str
 baseline:Mapping[str,object]
 optimized:Mapping[str,object]
 baseline_quality:Mapping[str,object]
 optimized_quality:Mapping[str,object]
def _quality(q):
 if q.get("status")!="passed": raise ValueError("quality receipt must have passed status")
 v=q.get("weighted_score")
 if type(v) not in (int,float) or not math.isfinite(float(v)) or not 0<=v<=100: raise ValueError("invalid quality score")
 return float(v)
def _metrics(r):
 d={}
 for k in METRICS:
  v=r.get(k)
  if type(v) not in (int,float) or not math.isfinite(float(v)) or v<0: raise ValueError("missing or invalid measured metric: "+k)
  d[k]=float(v)
 if type(r.get("policy_violations")) is not int or r["policy_violations"]<0: raise ValueError("invalid policy violation count")
 return d
def analyze_campaign(trials:Sequence[PairedTrial],*,minimum_quality:float=0,max_quality_regression:float=0):
 if not trials: raise ValueError("campaign requires paired trials")
 ids=[t.trial_id for t in trials]
 if any(not isinstance(i,str) or not i.strip() for i in ids) or len(set(ids))!=len(ids): raise ValueError("trial IDs must be unique")
 deltas={k:[] for k in METRICS}; qd=[]; vd=[]; detail=[]
 for t in trials:
  verify_paired_run_identity(t.baseline,t.optimized)
  b=_metrics(t.baseline);o=_metrics(t.optimized);bq=_quality(t.baseline_quality);oq=_quality(t.optimized_quality)
  if t.baseline_quality.get("task_digest")!=t.baseline.get("task_digest") or t.optimized_quality.get("task_digest")!=t.optimized.get("task_digest"): raise ValueError("rubric receipt task mismatch")
  for k in METRICS: deltas[k].append(b[k]-o[k])
  qd.append(oq-bq);vd.append(int(t.optimized["policy_violations"])-int(t.baseline["policy_violations"]))
  detail.append({"trial_id":t.trial_id,"quality_delta":qd[-1],"policy_violation_delta":vd[-1]})
 def stat(a):
  m=sum(a)/len(a);se=math.sqrt(sum((x-m)**2 for x in a)/(len(a)-1))/math.sqrt(len(a)) if len(a)>1 else None
  return {"mean":m,"standard_error":se,"observations":len(a)}
 metrics={k:stat(v) for k,v in deltas.items()};metrics["quality_delta"]=stat(qd);metrics["policy_violation_delta"]=stat(vd)
 eligible=all(_quality(t.optimized_quality)>=minimum_quality for t in trials) and metrics["quality_delta"]["mean"]>=-max_quality_regression and all(x<=0 for x in vd)
 return {"trial_count":len(trials),"paired_trials":detail,"metrics":metrics,"promotion_eligible":eligible,"limitations":"Descriptive paired evidence only; no independent evaluation or promotion authorization."}
