"""Private global recovery audit evidence, separate from scoped Favorites.

Reuse the bounded private-file/locking mechanics, not the scoped wire codec.
Audit evidence never changes a transfer outcome or authorizes its replay.
"""

import copy
import re

import favorites_operations as private
import preset_favorites as files
import global_favorites_recovery as recovery


class AcknowledgementStore(private.AcknowledgementStore):
    """Instantiate only inside this target's global operations directory."""

    def _intent(self, inspection, request):
        try:
            request = recovery.validate_request(request)
            if type(inspection) is not dict or request != recovery.recovery_ack_request(inspection):
                raise ValueError
            return {"version": 1, "target_sha256": self.target,
                    "request": copy.deepcopy(request), "inspection": copy.deepcopy(inspection)}
        except Exception:  # noqa: BLE001 - only fixed private-storage errors escape
            raise private.OperationStoreError("invalid_context") from None

    def load_intent(self, acknowledgement_id, operation_id, plan_sha256):
        try:
            if (type(acknowledgement_id) is not str or re.fullmatch(r"[0-9a-f]{64}", acknowledgement_id) is None
                    or type(plan_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None
                    or type(operation_id) is not str or private.UUID.fullmatch(operation_id) is None):
                raise ValueError
            with files._Parent(self.directory) as parent:
                saved = files._decode(self._read(parent, "favorites-ack-" + acknowledgement_id + ".intent.json"))
                parent.assert_current()
            inspection, request = saved["inspection"], saved["request"]
            if (type(saved.get("version")) is not int or saved["version"] != 1
                    or saved != self._intent(inspection, request)
                    or request["acknowledgement_id"] != acknowledgement_id
                    or request["operation_id"] != operation_id or request["plan_sha256"] != plan_sha256):
                raise ValueError
            return copy.deepcopy(inspection), copy.deepcopy(request)
        except FileNotFoundError:
            raise private.OperationStoreError("missing") from None
        except Exception:  # noqa: BLE001 - damaged or foreign evidence is never authority
            raise private.OperationStoreError("invalid_context") from None

    def save_evidence(self, inspection, request, evidence):
        value = self._intent(inspection, request)
        try:
            params = request if evidence["op"] == "acknowledge" else {
                "op": "inspect", "runtime": evidence["runtime"], "subject": "global",
                "operation_id": request["operation_id"]}
            result = recovery.validate_result(params, evidence, request_id="0" * 32,
                                              response_id="0" * 32, status="ok")
            expected = {**inspection["record"], "acknowledgement": recovery.recovery_audit(request)}
            if result.data["record"] != expected:
                raise ValueError
            with files._Parent(self.directory) as parent:
                saved = files._decode(self._read(parent, "favorites-ack-" + request["acknowledgement_id"] + ".intent.json"))
                if type(saved.get("version")) is not int or saved["version"] != 1 or saved != value:
                    raise ValueError
                parent.assert_current()
            return self._write("favorites-ack-" + request["acknowledgement_id"] + ".evidence.json",
                {"version": 1, "target_sha256": self.target, "acknowledgement_id": request["acknowledgement_id"],
                 "classification": "administrative-audit-only", "evidence": evidence})
        except Exception:  # noqa: BLE001 - an audit failure never reverses a remote result
            raise private.OperationStoreError("invalid_completion") from None
