from typing import Any, Mapping


class ProviderEffectAdapter:
    """Trusted provider adapter contract."""

    def apply(self, operation: Any, payload: Mapping[str, Any], *, stable_key: str, payload_digest: str) -> Mapping[str, Any]:
        raise NotImplementedError

    def inspect(self, payload: Mapping[str, Any], *, stable_key: str, payload_digest: str) -> Mapping[str, Any]:
        raise NotImplementedError

    def verify_receipt(self, stable_key: str, evidence_ref: str, receipt: Mapping[str, Any]) -> bool:
        raise NotImplementedError
