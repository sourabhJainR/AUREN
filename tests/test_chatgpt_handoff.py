"""Tests for safe ChatGPT handoff and guarded proposal import."""
import json, subprocess, tempfile, unittest, hashlib
from pathlib import Path
from portable.chatgpt_handoff import export_task_package,load_proposal,validate_proposal,apply_approved_proposal,create_independent_project

class HandoffTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/"repo";self.root.mkdir()
  subprocess.run(["git","init"],cwd=self.root,check=True,capture_output=True)
  subprocess.run(["git","config","user.email","test@example.invalid"],cwd=self.root,check=True)
  subprocess.run(["git","config","user.name","AUREN tests"],cwd=self.root,check=True)
  (self.root/"README.md").write_text("# test\n");(self.root/"src").mkdir();(self.root/"src"/"app.py").write_text("x=1\n")
  subprocess.run(["git","add","."],cwd=self.root,check=True);subprocess.run(["git","commit","-m","init"],cwd=self.root,check=True,capture_output=True)
  self.sha=subprocess.run(["git","rev-parse","HEAD"],cwd=self.root,check=True,text=True,capture_output=True).stdout.strip()
 def tearDown(self): self.tmp.cleanup()
 def proposal(self,change=None,base=None):
  p=self.base/"proposal.json";p.write_text(json.dumps({"schema_version":1,"kind":"auren.chatgpt_proposal","base_commit":base or self.sha,"changes":[change or {"path":"src/app.py","content":"x=2\n"}]}));return p
 def test_export_exact_head_and_selected_files(self):
  out=self.base/"package.json";export_task_package(self.root,out,task="Fix",acceptance_criteria=["tests pass"],files=["src/app.py"])
  data=json.loads(out.read_text());self.assertEqual(self.sha,data["base_commit"]);self.assertEqual(["src/app.py"],[x["path"] for x in data["files"]])
 def test_export_redacts_secret(self):
  (self.root/"README.md").write_text("token=ghp_"+"a"*30)
  out=self.base/"package.json";export_task_package(self.root,out,task="review",acceptance_criteria=["safe"],files=["src/app.py"])
  self.assertNotIn("ghp_"+"a"*30,json.loads(out.read_text())["instructions"][0]["content"])
 def test_reject_paths_and_secret_payload(self):
  for path in ("../x","/etc/passwd",".auren/token",".env","src\\evil.py"):
   with self.assertRaises(ValueError): load_proposal(self.proposal({"path":path,"content":"ok"}))
  with self.assertRaises(ValueError): load_proposal(self.proposal({"path":"src/app.py","content":"API_KEY=sk-"+"a"*30}))
 def test_direct_validator_cannot_bypass_secret_or_duplicate_checks(self):
  secret={"schema_version":1,"kind":"auren.chatgpt_proposal","base_commit":self.sha,"changes":[{"path":"src/app.py","content":"token=ghp_"+"a"*30}]}
  with self.assertRaises(ValueError): validate_proposal(self.root,secret)
  duplicate={"schema_version":1,"kind":"auren.chatgpt_proposal","base_commit":self.sha,"changes":[{"path":"src/app.py","content":"x=2"},{"path":"src/app.py","content":"x=3"}]}
  with self.assertRaises(ValueError): validate_proposal(self.root,duplicate)
 def test_stale_base_rejected(self):
  with self.assertRaisesRegex(ValueError,"stale proposal"): validate_proposal(self.root,load_proposal(self.proposal(base="0"*40)))
 def test_guard_and_approval_required_and_worktree_isolated(self):
  rejected=load_proposal(self.proposal())
  with self.assertRaises(PermissionError):
   apply_approved_proposal(self.root,rejected,approval_verifier=lambda p,d:{"status":"approved","proposal_sha256":d,"evidence_ref":"approval://1"},guard_verifier=lambda w,p:{"status":"denied"},worktree_root=self.base/"worktrees")
  # A denied Guard worktree is retained for review. A changed proposal gets a new digest/worktree.
  proposal=load_proposal(self.proposal({"path":"src/app.py","content":"x=3\\n"}))
  digest=hashlib.sha256(json.dumps(proposal,sort_keys=True,separators=(",",":")).encode()).hexdigest()
  result=apply_approved_proposal(self.root,proposal,approval_verifier=lambda p,d:{"status":"approved","proposal_sha256":d,"evidence_ref":"approval://2"},guard_verifier=lambda w,p:{"status":"approved","proposal_sha256":digest,"evidence_ref":"guard://2","policy_digest":"a"*64},worktree_root=self.base/"worktrees")
  self.assertEqual("applied_in_isolated_worktree",result["status"]);self.assertEqual("x=1\\n",(self.root/"src/app.py").read_text());self.assertEqual("x=3\\n",(Path(result["worktree"])/"src/app.py").read_text())
  rejected_digest=hashlib.sha256(json.dumps(rejected,sort_keys=True,separators=(",",":")).encode()).hexdigest()
  self.assertTrue((self.base/"worktrees"/rejected_digest[:16]).exists())
 def test_independent_project(self):
  target=self.base/"new";result=create_independent_project(target,name="new")
  self.assertTrue((target/".git").is_dir());self.assertTrue((target/".auren"/"project.json").is_file());self.assertEqual("initialized",result["status"])
  self.assertRegex(result["base_commit"],r"^[0-9a-f]{40}$")
  profile=json.loads((target/".auren"/"project.json").read_text())
  self.assertEqual("new",profile["project_name"])
  ignored=subprocess.run(["git","check-ignore",".auren/project.json"],cwd=target,text=True,capture_output=True)
  self.assertEqual(0,ignored.returncode)
  self.assertEqual("main",subprocess.run(["git","branch","--show-current"],cwd=target,text=True,capture_output=True,check=True).stdout.strip())

 def test_worktree_store_symlink_is_rejected(self):
  proposal=load_proposal(self.proposal())
  link=self.base/"linked-worktrees";link.symlink_to(self.base/"real-worktrees",target_is_directory=True)
  with self.assertRaises(PermissionError):
   apply_approved_proposal(self.root,proposal,approval_verifier=lambda p,d:{"status":"approved","proposal_sha256":d,"evidence_ref":"approval://1"},guard_verifier=lambda w,p:{"status":"approved","proposal_sha256":d,"evidence_ref":"guard://1","policy_digest":"a"*64},worktree_root=link)

if __name__=="__main__": unittest.main()
