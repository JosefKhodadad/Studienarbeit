from __future__ import annotations

from copy import deepcopy


class InMemoryImportRepository:
    def __init__(self) -> None:
        self._reports: dict[str, dict] = {}
        self._patient_latest_report: dict[str, str] = {}
        self._current_report_id: str | None = None

    def save_dashboard_payload(self, payload: dict) -> dict:
        report_id = payload["report"]["id"]
        patient_id = payload["patient"]["id"]
        self._reports[report_id] = deepcopy(payload)
        self._patient_latest_report[patient_id] = report_id
        self._current_report_id = report_id
        return deepcopy(payload)

    def get_dashboard_payload(self, report_id: str) -> dict:
        if report_id not in self._reports:
            raise KeyError(report_id)
        return deepcopy(self._reports[report_id])

    def get_current_dashboard_payload(self) -> dict:
        if not self._current_report_id:
            raise KeyError("current_report")
        return self.get_dashboard_payload(self._current_report_id)

    def get_patient_profile(self, patient_id: str) -> dict:
        report_id = self._patient_latest_report.get(patient_id)
        if not report_id:
            raise KeyError(patient_id)
        payload = self.get_dashboard_payload(report_id)
        return {
            "patient": payload["patient"],
            "report": payload["report"],
        }
