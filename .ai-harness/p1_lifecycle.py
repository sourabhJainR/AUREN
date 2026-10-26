#!/usr/bin/env python3
"""Bridge P0/P1 contracts into the production harness lifecycle."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parent.parent
RUNTIME=ROOT/".ai-harness"/"runtime"
sys.path.insert(0,str(RUNTIME))
sys.path.insert(0,str(ROOT/".ai-harness"))
from p0 import add_evidence, evidence, new_state, proof_bundle, record_outcome, save_json, verification
from p1 import affected_profile_fields, graph_edge, graph_node, profile, regression_case, save
from regression_genome import candidate_case
from state_validator import validate_state
from artifact_chain import build_artifacts

def _task_id(task: str) -> str:
    return "task-"+hashlib.sha256(task.encode()).hexdigest()[:12]

def _facts(raw: dict[str,Any]) -> dict[str,dict[str,Any]]:
    facts={}
    for key,value in raw.items():
        if key in ("profile_error",): continue
        facts[key]={"status":"observed" if value else "unknown","value":json.dumps(value,sort_keys=True) if isinstance(value,(dict,list)) else str(value),"evidence_ids":[]}
    return facts

def start(run_dir: Path, task: str, source: str, repo_profile: dict[str,Any], route: dict[str,Any], intent_contract: dict[str,Any]|None=None) -> dict[str,Any]:
    state=new_state(_task_id(task),task,source)
    state["status"]="investigating"
    route_ev=evidence("tool","router","route selected",snapshot=json.dumps(route,sort_keys=True),confidence="high",provenance="engine.route")
    add_evidence(state,route_ev)
    state["repo_facts"].append({"evidence_id":route_ev["id"]})
    if intent_contract is None:
        contract_path=run_dir/"intent-contract.json"
        if contract_path.is_file():
            contract=json.loads(contract_path.read_text(encoding="utf-8"))
        else:
            contract={"goal":task,"intent_digest":hashlib.sha256(task.encode()).hexdigest()[:16]}
    else:
        contract=intent_contract
    artifacts=build_artifacts(run_dir,contract,route=route)
    artifact_ev=evidence("runtime","artifact-chain","intent/spec/plan artifacts created",snapshot=json.dumps(artifacts,sort_keys=True),confidence="high",provenance="p1_lifecycle.start")
    add_evidence(state,artifact_ev)
    state["repo_facts"].append({"evidence_id":artifact_ev["id"]})
    dna=profile(str(ROOT),_facts(repo_profile))
    state_path=run_dir/"engineering-state.json"
    dna_path=run_dir/"repository-dna.json"
    save_json(state_path,state); save(dna_path,dna)
    return state

def finish(run_dir: Path, manifest: dict[str,Any]) -> dict[str,Any]:
    state_path=run_dir/"engineering-state.json"
    state=json.loads(state_path.read_text(encoding="utf-8"))
    changed=[]
    diff=manifest.get("git_diff","")
    for line in diff.splitlines():
        line=line.strip()
        if line and not line.startswith("$") and not line.startswith("ERROR"):
            if line.endswith((".py",".cs",".java",".ts",".tsx",".js",".go",".rs",".md",".json",".toml",".yml",".yaml")):
                changed.append(line)
    changed=sorted(set(changed))
    state["changeset"]["files"]=changed
    state["changeset"]["diff_identity"]=hashlib.sha256(diff.encode()).hexdigest()[:16]
    state["status"]="completed" if manifest.get("status")=="completed" else "blocked"
    validation=manifest.get("validation",{})
    ver_status="passed" if validation.get("passed",True) else "failed"
    verification(state,"harness-validation",ver_status,details=json.dumps(validation,sort_keys=True))
    outcome_status=manifest.get("outcome_status")
    if outcome_status not in ("accepted","rejected","partial","unknown"):
        outcome_status="accepted" if state["status"]=="completed" and ver_status=="passed" else "unknown"
    outcome_ev=evidence("runtime","lifecycle","Task outcome recorded",snapshot=json.dumps({"status":outcome_status,"review":manifest.get("review_result",""),"production":manifest.get("production_result","")},sort_keys=True),confidence="high",provenance="p1_lifecycle.finish")
    add_evidence(state,outcome_ev)
    record_outcome(state,outcome_status,user_acceptance=manifest.get("user_acceptance",""),review_result=manifest.get("review_result",""),production_result=manifest.get("production_result",""),regressions=manifest.get("regressions",[]),follow_up=manifest.get("follow_up",[]),metrics=manifest.get("metrics",{}),evidence_ids=[outcome_ev["id"]])
    save_json(state_path,state)
    errors=validate_state(state)
    if errors:
        save_json(run_dir/"state-validation-errors.json", {"valid":False,"errors":errors})
        raise RuntimeError("Engineering State Ledger validation failed: " + "; ".join(errors))
    proof=proof_bundle(state)
    nodes=[graph_node("requirement",state["task_id"]),graph_node("changeset",state["changeset"]["diff_identity"] or "none"),graph_node("proof",proof["proof_id"]),graph_node("outcome",outcome_status)]
    edges=[graph_edge(nodes[0]["id"],"implemented_by",nodes[1]["id"],[e["id"] for e in state["evidence"]]),graph_edge(nodes[1]["id"],"verified_by",nodes[2]["id"],[]),graph_edge(nodes[2]["id"],"resulted_in",nodes[3]["id"],[outcome_ev["id"]])]
    regression=regression_case("task-completion:"+state["task_id"],state["status"],["preserve protected behavior","proof required"])
    candidates=[]
    raw_candidates=manifest.get("regression_candidates",[])
    if not isinstance(raw_candidates,list):
        candidates.append({"status":"rejected","reason":"candidate_list_invalid"})
    else:
        for item in raw_candidates:
            if not isinstance(item,dict):
                candidates.append({"status":"rejected","reason":"candidate_must_be_object"})
                continue
            try:
                candidates.append(candidate_case(
                    item.get("source_kind"),item.get("source_ref"),item.get("trigger"),item.get("expected"),
                    item.get("constraints",[]),proposer_id=manifest.get("proposer_id","unknown")
                ))
            except (TypeError,ValueError):
                candidates.append({"status":"rejected","reason":"candidate_validation_failed"})
    genome={"version":"1.2","case":regression,"candidates":candidates,
            "promotion_policy":{"auto_promote":False,"deterministic_passes":2,
                                "independent_sealed_holdout":True,"human_approval":True,
                                "verified_evidence_required":True},
            "result":{"status":state["status"],"outcome":outcome_status},
            "affected_profile_fields":affected_profile_fields(changed)}
    save_json(run_dir/"proof-bundle.json",proof); save_json(run_dir/"proof-graph.json",{"version":"1.1","nodes":nodes,"edges":edges}); save_json(run_dir/"regression-genome.json",genome)
    return proof
