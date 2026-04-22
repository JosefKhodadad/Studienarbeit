from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InMemoryImportRepository:
    """In-memory store for imported Hilo reports.

    Supports multiple reports per session (one per imported PDF or manual
    bootstrap). One report is "current" and serves the dashboard columns /
    monthly summary. All reports remain addressable by id for the multi-report
    UI and the cross-report unified view.
    """

    def __init__(self) -> None:
        self._reports: dict[str, dict] = {}
        self._report_imported_at: dict[str, str] = {}
        self._patient_latest_report: dict[str, str] = {}
        self._current_report_id: str | None = None

    # --- writes ---------------------------------------------------------

    def save_dashboard_payload(self, payload: dict) -> dict:
        report_id = payload["report"]["id"]
        patient_id = payload["patient"]["id"]
        stamped_payload = self._stamp_import_time(payload, report_id)
        self._reports[report_id] = deepcopy(stamped_payload)
        self._patient_latest_report[patient_id] = report_id
        self._current_report_id = report_id
        return deepcopy(stamped_payload)

    def replace_current_payload(self, payload: dict) -> dict:
        """Replace the currently-selected report without touching other data."""
        if not self._current_report_id:
            raise KeyError("current_report")
        report_id = payload["report"]["id"]
        if report_id != self._current_report_id:
            raise ValueError("report id mismatch on replace_current_payload")
        preserved_at = self._report_imported_at.get(report_id)
        stamped = deepcopy(payload)
        if preserved_at and isinstance(stamped.get("report"), dict):
            stamped["report"]["imported_at"] = preserved_at
        self._reports[report_id] = deepcopy(stamped)
        return deepcopy(stamped)

    def delete_report(self, report_id: str) -> dict:
        if report_id not in self._reports:
            raise KeyError(report_id)
        removed = self._reports.pop(report_id)
        self._report_imported_at.pop(report_id, None)

        # Clear patient->report reverse index entries that pointed at this report
        for patient_id, latest_id in list(self._patient_latest_report.items()):
            if latest_id == report_id:
                self._patient_latest_report.pop(patient_id, None)

        if self._current_report_id == report_id:
            # Promote the most recently imported remaining report to "current".
            remaining = sorted(
                self._reports.keys(),
                key=lambda rid: self._report_imported_at.get(rid, ""),
                reverse=True,
            )
            self._current_report_id = remaining[0] if remaining else None

        # Re-establish patient->report mapping from remaining reports
        for rid, payload in self._reports.items():
            pid = payload.get("patient", {}).get("id")
            if not pid:
                continue
            existing = self._patient_latest_report.get(pid)
            if existing is None or self._report_imported_at.get(rid, "") > self._report_imported_at.get(existing, ""):
                self._patient_latest_report[pid] = rid

        return deepcopy(removed)

    def set_current_report(self, report_id: str) -> dict:
        if report_id not in self._reports:
            raise KeyError(report_id)
        self._current_report_id = report_id
        return self.get_dashboard_payload(report_id)

    # --- reads ----------------------------------------------------------

    def get_dashboard_payload(self, report_id: str) -> dict:
        if report_id not in self._reports:
            raise KeyError(report_id)
        return deepcopy(self._reports[report_id])

    def get_current_dashboard_payload(self) -> dict:
        if not self._current_report_id:
            raise KeyError("current_report")
        return self.get_dashboard_payload(self._current_report_id)

    def list_reports(self) -> list[dict]:
        """Return lightweight report metadata sorted chronologically (newest first)."""
        items: list[dict] = []
        for report_id, payload in self._reports.items():
            report = payload.get("report", {}) or {}
            patient = payload.get("patient", {}) or {}
            items.append(
                {
                    "id": report_id,
                    "patient_id": patient.get("id"),
                    "patient_name": patient.get("full_name"),
                    "report_month": report.get("report_month"),
                    "report_year": report.get("report_year"),
                    "source": report.get("source", "unknown_source"),
                    "file_name": report.get("file_name"),
                    "measurement_count": len(payload.get("measurements") or []),
                    "imported_at": self._report_imported_at.get(report_id),
                    "is_current": report_id == self._current_report_id,
                }
            )
        items.sort(key=lambda item: item.get("imported_at") or "", reverse=True)
        return items

    def iter_all_reports(self) -> list[dict]:
        """Yield deep copies of all stored payloads sorted by import time (oldest first)."""
        ordered = sorted(
            self._reports.items(),
            key=lambda kv: self._report_imported_at.get(kv[0], ""),
        )
        return [deepcopy(payload) for _, payload in ordered]

    @property
    def current_report_id(self) -> str | None:
        return self._current_report_id

    def get_patient_profile(self, patient_id: str) -> dict:
        report_id = self._patient_latest_report.get(patient_id)
        if not report_id:
            raise KeyError(patient_id)
        payload = self.get_dashboard_payload(report_id)
        return {
            "patient": payload["patient"],
            "report": payload["report"],
        }

    # --- helpers --------------------------------------------------------

    def _stamp_import_time(self, payload: dict, report_id: str) -> dict:
        stamped = deepcopy(payload)
        imported_at = self._report_imported_at.get(report_id) or _now_iso()
        self._report_imported_at[report_id] = imported_at
        if isinstance(stamped.get("report"), dict):
            stamped["report"]["imported_at"] = imported_at
        return stamped
