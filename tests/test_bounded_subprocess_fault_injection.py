from __future__ import annotations
import os, sys, tempfile, threading, time, unittest
from pathlib import Path
from portable.bounded_subprocess import run_bounded_command

class BoundedSubprocessFaultInjectionTests(unittest.TestCase):
    def test_hung_command_is_terminated_within_budget_and_not_success(self):
        start=time.monotonic()
        result=run_bounded_command([sys.executable,"-c","import time; time.sleep(30)"],timeout_seconds=0.2,terminate_grace_seconds=0.1)
        self.assertEqual("timed_out",result.status); self.assertFalse(result.succeeded)
        self.assertLess(time.monotonic()-start,3.0)
    def test_parent_deadline_caps_child_timeout(self):
        result=run_bounded_command([sys.executable,"-c","import time; time.sleep(30)"],timeout_seconds=10,deadline_monotonic=time.monotonic()+0.2,terminate_grace_seconds=0.1)
        self.assertEqual("timed_out",result.status); self.assertEqual("parent_deadline_exceeded",result.reason)
        self.assertLess(result.elapsed_seconds,3.0)
    def test_cancellation_terminates_command_and_is_explicit(self):
        cancel=threading.Event()
        coordinator=threading.Thread(target=lambda:(time.sleep(0.15),cancel.set()),daemon=True); coordinator.start()
        result=run_bounded_command([sys.executable,"-c","import time; time.sleep(30)"],timeout_seconds=5,cancellation_event=cancel,terminate_grace_seconds=0.1)
        coordinator.join(timeout=1)
        self.assertEqual("cancelled",result.status); self.assertEqual("cancellation_requested",result.reason)
    def test_cancellation_kills_descendant_before_it_can_commit_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child-survived"
            child = "import pathlib,sys,time; time.sleep(1.0); pathlib.Path(sys.argv[1]).write_text('survived')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]]); time.sleep(30)"
            cancel = threading.Event()
            coordinator = threading.Thread(
                target=lambda: (time.sleep(0.25), cancel.set()), daemon=True,
            )
            coordinator.start()
            result = run_bounded_command(
                [sys.executable, "-c", parent, str(marker), child],
                timeout_seconds=5, cancellation_event=cancel,
                terminate_grace_seconds=0.1,
            )
            coordinator.join(timeout=1)
            self.assertEqual("cancelled", result.status)
            time.sleep(1.2)
            self.assertFalse(
                marker.exists(),
                "a descendant must not survive cancellation and commit output afterward",
            )

    def test_output_capture_is_bounded_and_marks_truncation(self):
        result=run_bounded_command([sys.executable,"-c","print('x'*200000); print('e'*200000,file=__import__('sys').stderr)"],timeout_seconds=3,max_output_bytes=1024)
        self.assertEqual("completed",result.status); self.assertEqual(0,result.returncode)
        self.assertLessEqual(len(result.stdout.encode()),1024); self.assertLessEqual(len(result.stderr.encode()),1024)
        self.assertTrue(result.stdout_truncated); self.assertTrue(result.stderr_truncated)
    @unittest.skipIf(os.name=="nt","POSIX process-group descendant assertion")
    def test_timeout_kills_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file=Path(tmp)/"child.pid"
            child="import time; time.sleep(30)"
            parent="import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
            result=run_bounded_command([sys.executable,"-c",parent,str(pid_file),child],timeout_seconds=0.3,terminate_grace_seconds=0.1)
            self.assertEqual("timed_out",result.status); pid=int(pid_file.read_text())
            try: os.kill(pid,0)
            except ProcessLookupError: return
            stat=Path(f"/proc/{pid}/stat")
            if stat.exists(): self.assertEqual("Z",stat.read_text().split()[2],"descendant must not remain running")
    def test_pre_cancelled_request_does_not_spawn_process(self):
        cancel=threading.Event(); cancel.set()
        result=run_bounded_command([sys.executable,"-c","raise SystemExit(99)"],timeout_seconds=1,cancellation_event=cancel)
        self.assertEqual("cancelled",result.status); self.assertIsNone(result.returncode)
    def test_nonzero_exit_is_never_reported_as_success(self):
        result=run_bounded_command([sys.executable,"-c","raise SystemExit(7)"],timeout_seconds=2)
        self.assertEqual("completed",result.status); self.assertEqual(7,result.returncode); self.assertFalse(result.succeeded)
    @unittest.skipUnless(os.name == "nt", "Windows Job Object containment")
    def test_timeout_kills_windows_job_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child-survived"
            child = "import pathlib,sys,time; time.sleep(1.0); pathlib.Path(sys.argv[1]).write_text('survived')"
            parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]])"
            result = run_bounded_command(
                [sys.executable, "-c", parent, str(marker), child],
                timeout_seconds=0.25, terminate_grace_seconds=0.1,
            )
            self.assertEqual("timed_out", result.status)
            time.sleep(1.2)
            self.assertFalse(marker.exists(), "Windows child escaped its kill-on-close Job Object")

    @unittest.skipIf(os.name == "nt", "POSIX RLIMIT_AS semantics")
    def test_configured_memory_budget_is_applied_before_child_exec(self):
        budget = 512 * 1024 * 1024
        result = run_bounded_command(
            [sys.executable, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"],
            timeout_seconds=3, max_memory_bytes=budget,
        )
        self.assertTrue(result.succeeded, result.stderr)
        self.assertEqual(str(budget), result.stdout.strip())

    def test_memory_budget_rejects_invalid_values_before_spawn(self):
        for value in (0, -1, True, 1024, 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_memory_bytes"):
                run_bounded_command(
                    [sys.executable, "-c", "raise SystemExit(99)"],
                    timeout_seconds=1, max_memory_bytes=value,
                )

    def test_shell_strings_and_invalid_budgets_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"argv"): run_bounded_command("sleep 10",timeout_seconds=1)
        for value in (0,-1,True,float("nan"),float("inf")):
            with self.subTest(value=value),self.assertRaises(ValueError): run_bounded_command([sys.executable,"-c","pass"],timeout_seconds=value)

if __name__=="__main__": unittest.main()
