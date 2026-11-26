from portable.browser_qa_ci_adapter import poll_browser_qa, persist_receipt
SHA="a"*40

def test_pass_requires_exact_sha_and_terminal_success():
    receipt=poll_browser_qa("owner/repo",1,SHA,token="fake",request=lambda u,t:{
      "head_sha":SHA,"status":"completed","conclusion":"success","html_url":"https://example/run"})
    assert receipt["state"]=="passed"

def test_sha_mismatch_is_not_success():
    receipt=poll_browser_qa("owner/repo",1,SHA,token="fake",request=lambda u,t:{
      "head_sha":"b"*40,"status":"completed","conclusion":"success"})
    assert receipt["state"]=="sha_mismatch"

def test_completed_failure_is_failed():
    receipt=poll_browser_qa("owner/repo",1,SHA,token="fake",request=lambda u,t:{
      "head_sha":SHA,"status":"completed","conclusion":"failure"})
    assert receipt["state"]=="failed"

def test_running_run_times_out_without_unbounded_poll():
    calls=[0]
    def clock(): calls[0]+=1; return 0 if calls[0]==1 else 2
    receipt=poll_browser_qa("owner/repo",1,SHA,token="fake",timeout_seconds=1,
      interval_seconds=1,request=lambda u,t:{"head_sha":SHA,"status":"in_progress"},
      clock=clock,sleep=lambda _:None)
    assert receipt["state"]=="timeout"

def test_receipt_atomically_persisted(tmp_path):
    path=persist_receipt({"state":"passed","expected_sha":SHA},tmp_path/"receipt.json")
    assert path.exists() and not tmp_path.joinpath("receipt.json.tmp").exists()
