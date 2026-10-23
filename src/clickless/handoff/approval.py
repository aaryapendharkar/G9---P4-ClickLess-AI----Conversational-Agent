"""Approval gate: no external action without an explicit yes from the user. Owner: Aarya.

Every call is appended to an audit log so the evaluation can prove "zero actions without
approval" (proposal §V-C). Offline experiments never call approve().
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import repo_path


class ApprovalGate:
    def __init__(self, audit_log: str = "results/handoff_audit.jsonl") -> None:
        self.audit_log = repo_path(audit_log)

    def record(self, request_id: str, approved: bool, n_urls: int) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.audit_log, "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "request_id": request_id,
                                "approved": approved, "urls": n_urls}) + "\n")

    @staticmethod
    def audit_path_exists(path: Path) -> bool:
        return path.exists()
