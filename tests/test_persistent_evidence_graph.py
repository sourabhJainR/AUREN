import multiprocessing
import os
import json
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def raises(exc, match=None):
    try:
        yield
    except exc as error:
        if match is not None and match not in str(error):
            raise AssertionError(f"expected {match!r} in {error!s}")
    else:
        raise AssertionError(f"expected {exc.__name__} to be raised")
from portable.persistent_memory import PersistentMemory
from portable.persistent_evidence_graph import PersistentEvidenceGraph



def _crash_after_evidence_edge_commit(database, project, source_digest, target_digest):
    memory = PersistentMemory(Path(database), require_approval=False)
    graph = PersistentEvidenceGraph(memory, project)
    source = graph.add_node("source", source_digest)
    target = graph.add_node("result", target_digest)
    persist_edge = graph.add_edge

    def commit_then_die(left, relation, right):
        persist_edge(left, relation, right)
        os._exit(87)

    graph.add_edge = commit_then_die
    graph.add_edge(source, "produced", target)
    os._exit(88)

def graph(tmp_path):
    return PersistentEvidenceGraph(PersistentMemory(Path(tmp_path)/"m.db",require_approval=False),"hws")

def test_content_addressed_lineage(tmp_path):
    g=graph(tmp_path)
    a=g.add_node("campaign","abc",{"source":"external"})
    b=g.add_node("failure","def")
    e=g.add_edge(a,"produced",b)
    assert e.edge_digest==g.edge_digest(a.node_id,"produced",b.node_id)
    assert g.lineage(a.node_id,direction="out")[0].target_id==b.node_id


def test_content_addressed_node_rejects_conflicting_metadata_without_mutation(tmp_path):
    g=graph(tmp_path)
    original=g.add_node("run","digest-123",{"source":"trusted","revision":"abc"})
    # Existing releases serialized metadata with default JSON spacing.
    with g.memory._lock, g.memory._connection() as db:
        db.execute(
            "UPDATE evidence_nodes SET metadata_json=? WHERE project=? AND node_id=?",
            (json.dumps(dict(original.metadata), sort_keys=True), g.project, original.node_id),
        )
    # Identity-only lookups return the canonical persisted metadata.
    assert g.add_node("run","digest-123") == original
    duplicate=g.add_node("run","digest-123",{"revision":"abc","source":"trusted"})
    assert duplicate == original
    with raises(ValueError,match="metadata conflict"):
        g.add_node("run","digest-123",{"source":"untrusted","revision":"abc"})
    assert g.get_node(original.node_id) == original


def test_process_death_after_edge_commit_replays_one_canonical_edge(tmp_path):
    database = str(tmp_path / "m.db")
    project = "crash-recovery"
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_evidence_edge_commit,
        args=(database, project, "source-digest", "result-digest"),
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(3)
        raise AssertionError("evidence edge crash fixture exceeded its time budget")
    assert process.exitcode == 87

    restarted = PersistentEvidenceGraph(
        PersistentMemory(Path(database), require_approval=False), project
    )
    source = restarted.add_node("source", "source-digest")
    target = restarted.add_node("result", "result-digest")
    recovered = restarted.add_edge(source, "produced", target)
    replayed = restarted.add_edge(source, "produced", target)
    assert recovered == replayed
    assert restarted.lineage(source.node_id, direction="out") == (recovered,)
    assert restarted.lineage(target.node_id, direction="in") == (recovered,)

def test_rejects_unregistered_node(tmp_path):
    g=graph(tmp_path)
    a=g.add_node("campaign","abc")
    b=type(a)("fake","failure","def")
    with raises(ValueError,match="unregistered"):
        g.add_edge(a,"produced",b)

def test_lineage_direction_and_budget(tmp_path):
    g=graph(tmp_path)
    a=g.add_node("a","1"); b=g.add_node("b","2"); g.add_edge(a,"next",b)
    assert len(g.lineage(b.node_id,direction="in"))==1
    with raises(ValueError): g.lineage(a.node_id,max_edges=0)
