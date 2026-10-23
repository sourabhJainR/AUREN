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

def test_lineage_fails_closed_on_corrupt_edge_digest(tmp_path):
    g=graph(tmp_path)
    a=g.add_node("source","1"); b=g.add_node("result","2")
    edge=g.add_edge(a,"produced",b)
    with g.memory._lock, g.memory._connection() as db:
        db.execute(
            "UPDATE evidence_edges SET edge_digest=? WHERE project=? AND source_id=? AND relation=? AND target_id=?",
            ("not-the-canonical-digest", g.project, edge.source_id, edge.relation, edge.target_id),
        )
    with raises(ValueError,match="edge digest is corrupt"):
        g.lineage(a.node_id,direction="out")


def test_lineage_fails_closed_when_edge_endpoint_is_missing(tmp_path):
    g=graph(tmp_path)
    a=g.add_node("source","1"); b=g.add_node("result","2")
    edge=g.add_edge(a,"produced",b)
    with g.memory._lock, g.memory._connection() as db:
        db.execute(
            "DELETE FROM evidence_nodes WHERE project=? AND node_id=?",
            (g.project, b.node_id),
        )
    with raises(ValueError,match="missing node"):
        g.lineage(a.node_id,direction="out")


def test_node_reads_fail_closed_on_corrupt_content_addressed_identity(tmp_path):
    g=graph(tmp_path)
    original=g.add_node("run", "trusted-digest", {"source": "trusted"})
    with g.memory._lock, g.memory._connection() as db:
        db.execute(
            "UPDATE evidence_nodes SET digest=? WHERE project=? AND node_id=?",
            ("tampered-digest", g.project, original.node_id),
        )
    with raises(ValueError, match="identity is corrupt"):
        g.get_node(original.node_id)
    with raises(ValueError, match="identity is corrupt"):
        g.nodes()


def test_node_reads_fail_closed_on_corrupt_metadata(tmp_path):
    g=graph(tmp_path)
    original=g.add_node("run", "trusted-digest", {"source": "trusted"})
    with g.memory._lock, g.memory._connection() as db:
        db.execute(
            "UPDATE evidence_nodes SET metadata_json=? WHERE project=? AND node_id=?",
            ("{invalid-json", g.project, original.node_id),
        )
    with raises(ValueError, match="metadata"):
        g.get_node(original.node_id)
    with raises(ValueError, match="metadata"):
        g.nodes()

