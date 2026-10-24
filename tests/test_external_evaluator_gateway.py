from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from portable.external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from portable.sealed_arena_boundary import ExternalOutcomeReceipt, SealedArenaBoundary, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_arena_exchange import export_outcome_receipt
from portable.sealed_arena_replay import ReplayRejected, SealedArenaReplayLedger
from portable.sealed_corpus_provenance import SealedCorpusProvenance


class ExternalEvaluatorGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = SealedArenaReplayLedger(Path(self.tempdir.name) / "replay.sqlite3")
        base = SealedCampaignRequest("campaign-d", "corpus-d", (SealedCaseEnvelope("case-1", "task-d", "input-d", "env-d", True),), "nonce-1")
        provenance = SealedCorpusProvenance("corpus-d", base.manifest_digest, "https://benchmarks.example.org/holdout/v1", "sealed_holdout", "clean", "operator", "attestation-d")
        self.request = replace(base, provenance=provenance)
        self.receipt = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),), run_nonce="nonce-1", request_digest=self.request.request_digest, contamination_free=True, independent_oracle=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_production_gateway_requires_os_isolation_adapter(self) -> None:
        class TrustedBoundary(SealedArenaBoundary):
            @property
            def has_trusted_custody_verifier(self):
                return True

        boundary = TrustedBoundary(lambda _value: True, replay_ledger=self.ledger)
        with self.assertRaisesRegex(ValueError, "requires a production-ready OS isolation adapter"):
            ExternalEvaluatorGateway(boundary, self.ledger)

    def test_production_gateway_rejects_unverified_isolation_adapter(self) -> None:
        class TrustedBoundary(SealedArenaBoundary):
            @property
            def has_trusted_custody_verifier(self):
                return True

        class UnverifiedAdapter:
            production_ready = False

        boundary = TrustedBoundary(lambda _value: True, replay_ledger=self.ledger)
        with self.assertRaisesRegex(ValueError, "explicitly production-ready OS isolation adapter"):
            ExternalEvaluatorGateway(boundary, self.ledger, isolated_executor=UnverifiedAdapter())

    def test_gateway_rejects_protocol_only_boundary_by_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "trusted Ed25519 keyring.*signed holdout custody"):
            ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True), self.ledger)

    def test_gateway_rejects_custom_custody_callback_as_production_verifier(self) -> None:
        boundary = SealedArenaBoundary(
            lambda _value: True,
            verify_custody_signature=lambda _signer, _signature, _message: True,
        )
        with self.assertRaisesRegex(ValueError, "trusted Ed25519 keyring"):
            ExternalEvaluatorGateway(boundary, self.ledger)

    def test_gateway_uses_external_process_and_sealed_receipt(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        evidence = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), self.ledger, allow_test_verifier=True).evaluate(
            self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
        )
        self.assertEqual(evidence.holdout_pass_rate, 1.0)


    def test_gateway_rejects_mismatched_boundary_replay_ledger(self) -> None:
        other = SealedArenaReplayLedger(Path(self.tempdir.name) / "other-replay.sqlite3")
        boundary = SealedArenaBoundary(lambda value: value.signature == "sig", replay_ledger=self.ledger)
        with self.assertRaisesRegex(ValueError, "share the same replay ledger database"):
            ExternalEvaluatorGateway(boundary, other, allow_test_verifier=True)

    def test_gateway_and_boundary_can_share_one_replay_ledger(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        boundary = SealedArenaBoundary(lambda value: value.signature == "sig", replay_ledger=self.ledger)
        gateway = ExternalEvaluatorGateway(boundary, self.ledger, allow_test_verifier=True)
        evidence = gateway.evaluate(
            self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
        )
        self.assertEqual(evidence.holdout_pass_rate, 1.0)
        with self.assertRaises(ReplayRejected):
            gateway.evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
            )

    def test_nonzero_exit_fails_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True), self.ledger, allow_test_verifier=True).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", "raise SystemExit(7)"))
            )

    def test_external_process_does_not_inherit_secrets(self) -> None:
        os.environ["AUREN_SECRET"] = "hidden"
        try:
            payload = repr(export_outcome_receipt(self.receipt).decode("utf-8"))
            script = "import os,sys; sys.stdin.buffer.read(); sys.exit(3) if \"AUREN_SECRET\" in os.environ else None; sys.stdout.buffer.write(" + payload + ".encode())"
            evidence = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), self.ledger, allow_test_verifier=True).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
            )
            self.assertEqual(evidence.holdout_pass_rate, 1.0)
        finally:
            os.environ.pop("AUREN_SECRET", None)


    def test_replay_is_rejected_even_after_ledger_reopens(self) -> None:
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        gateway = ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), self.ledger, allow_test_verifier=True)
        gateway.evaluate(self.request, ExternalEvaluatorCommand((sys.executable, "-c", script)))
        reopened = SealedArenaReplayLedger(Path(self.tempdir.name) / "replay.sqlite3")
        with self.assertRaises(ReplayRejected):
            ExternalEvaluatorGateway(SealedArenaBoundary(lambda value: value.signature == "sig"), reopened, allow_test_verifier=True).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", script))
            )


    def test_concurrent_replay_claims_have_exactly_one_winner(self) -> None:
        barrier = threading.Barrier(2)

        def claim() -> str:
            barrier.wait(timeout=2)
            try:
                self.ledger.claim(
                    run_nonce="parallel-nonce",
                    campaign_digest="campaign-d",
                    corpus_digest="corpus-d",
                )
                return "accepted"
            except ReplayRejected:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]
        self.assertCountEqual(results, ["accepted", "rejected"])

    def test_receipt_nonce_must_match_request(self) -> None:
        wrong = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),), run_nonce="wrong-nonce", request_digest=self.request.request_digest, contamination_free=True, independent_oracle=True)
        with self.assertRaisesRegex(ValueError, "nonce"):
            SealedArenaBoundary(lambda value: value.signature == "sig").accept(self.request, wrong)


    def test_external_evaluator_output_budget_is_enforced_during_read(self) -> None:
        script = "import sys; sys.stdin.buffer.read(); sys.stdout.write('x' * 1000000)"
        gateway = ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True), self.ledger, allow_test_verifier=True)
        with self.assertRaisesRegex(ValueError, "exceeds output budget"):
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand((sys.executable, "-c", script), max_output_bytes=128),
            )

    def test_external_evaluator_timeout_fails_closed(self) -> None:
        script = "import sys,time; sys.stdin.buffer.read(); time.sleep(2)"
        gateway = ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True), self.ledger, allow_test_verifier=True)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand((sys.executable, "-c", script), timeout_seconds=0.05),
            )


    @unittest.skipUnless(os.name == "posix", "process-group termination is POSIX-specific")
    def test_timeout_kills_evaluator_descendants(self) -> None:
        marker = Path(self.tempdir.name) / "descendant-survived.txt"
        child_script = (
            "import pathlib,time; time.sleep(0.4); pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
        )
        parent_script = (
            "import subprocess,sys,time; sys.stdin.buffer.read(); "
            "subprocess.Popen([sys.executable, '-c', " + repr(child_script) + "]); time.sleep(60)"
        )
        gateway = ExternalEvaluatorGateway(
            SealedArenaBoundary(lambda _value: True), self.ledger, allow_test_verifier=True
        )
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand((sys.executable, "-c", parent_script), timeout_seconds=0.05),
            )
        time.sleep(0.6)
        self.assertFalse(marker.exists(), "an evaluator descendant survived the timeout")


    def test_interrupted_run_nonce_stays_burned_after_ledger_reopen(self) -> None:
        gateway = ExternalEvaluatorGateway(
            SealedArenaBoundary(lambda _value: True), self.ledger, allow_test_verifier=True
        )
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand(
                    (sys.executable, "-c", "import sys,time; sys.stdin.buffer.read(); time.sleep(2)"),
                    timeout_seconds=0.05,
                ),
            )

        reopened = SealedArenaReplayLedger(Path(self.tempdir.name) / "replay.sqlite3")
        payload = export_outcome_receipt(self.receipt).decode("utf-8")
        valid_script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(payload) + ")"
        with self.assertRaises(ReplayRejected):
            ExternalEvaluatorGateway(
                SealedArenaBoundary(lambda value: value.signature == "sig"),
                reopened,
                allow_test_verifier=True,
            ).evaluate(
                self.request, ExternalEvaluatorCommand((sys.executable, "-c", valid_script))
            )

    def test_invalid_receipt_does_not_echo_evaluator_output(self) -> None:
        secret = "DO_NOT_LEAK_EVALUATOR_STDOUT"
        script = "import sys; sys.stdin.buffer.read(); print(" + repr(secret) + ")"
        gateway = ExternalEvaluatorGateway(SealedArenaBoundary(lambda _value: True), self.ledger, allow_test_verifier=True)
        with self.assertRaises(ValueError) as caught:
            gateway.evaluate(
                self.request,
                ExternalEvaluatorCommand((sys.executable, "-c", script)),
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_command_rejects_interpreter_and_loader_environment_overrides(self) -> None:
        for key in ("PYTHONHOME", "pythonstartup", "LD_LIBRARY_PATH", "Node_Options", "BASH_ENV"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "environment override is not allowed"):
                ExternalEvaluatorCommand(
                    (sys.executable, "-c", "pass"),
                    environment=((key, "attacker-controlled"),),
                )

    def test_command_environment_keys_are_unique_ignoring_case(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique ignoring case"):
            ExternalEvaluatorCommand(
                (sys.executable, "-c", "pass"),
                environment=(("LANG", "C"), ("lang", "en_US.UTF-8")),
            )

    def test_command_rejects_nul_bytes_in_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty tuple"):
            ExternalEvaluatorCommand((sys.executable, "-c", "pass" + chr(0) + "bad"))

    def test_shell_is_not_used(self) -> None:
        command = ExternalEvaluatorCommand((sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"))
        self.assertEqual(command.argv[0], sys.executable)


if __name__ == "__main__":
    unittest.main()
