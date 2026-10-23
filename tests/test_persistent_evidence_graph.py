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
