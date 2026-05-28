"""Detect impact and float incidents from month-on-month task comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schedule_impact.models.schemas import IncidentType
from schedule_impact.normalize.p6_tasks import TaskSnapshot, finish_slip_days, match_tasks


@dataclass
class DetectedIncident:
    incident_id: str
    incident_type: IncidentType
    proj_id: str
    project_row_id: str
    reporting_period: str
    previous_period: str
    primary_task_id: str
    task_code: str
    task_name: str
    delay_days: float
    delay_metric: str
    finish_field_used: str
    is_critical: bool
    total_float_hours: float


def detect_incidents(
    current_tasks: list[dict[str, str]],
    previous_tasks: list[dict[str, str]],
    *,
    programme_id: str,
    reporting_period: str,
    previous_period: str,
    project_row_id: str | None = None,
    impact_slip_threshold: float = 0.0,
    float_slip_threshold: float = 1.0,
) -> list[DetectedIncident]:
    incidents: list[DetectedIncident] = []
    impact_seq = 0
    float_seq = 0
    min_threshold = min(impact_slip_threshold, float_slip_threshold)

    for current, previous in match_tasks(current_tasks, previous_tasks):
        if previous is None:
            continue
        slip = finish_slip_days(current, previous)
        if slip is None or slip < min_threshold:
            continue

        proj = current.proj_id or previous.proj_id
        row_id = project_row_id or proj or "unknown"

        if current.is_critical and slip > impact_slip_threshold:
            impact_seq += 1
            incidents.append(
                _build(
                    impact_seq,
                    IncidentType.IMPACT,
                    programme_id,
                    reporting_period,
                    previous_period,
                    row_id,
                    proj,
                    current,
                    slip,
                )
            )
        elif not current.is_critical and slip >= float_slip_threshold:
            float_seq += 1
            incidents.append(
                _build(
                    float_seq,
                    IncidentType.FLOAT,
                    programme_id,
                    reporting_period,
                    previous_period,
                    row_id,
                    proj,
                    current,
                    slip,
                )
            )

    return incidents


def _build(
    seq: int,
    incident_type: IncidentType,
    programme_id: str,
    reporting_period: str,
    previous_period: str,
    project_row_id: str,
    proj_id: str,
    task: TaskSnapshot,
    slip: float,
) -> DetectedIncident:
    return DetectedIncident(
        incident_id=f"{programme_id}-{reporting_period}-{incident_type.value}-{seq:04d}",
        incident_type=incident_type,
        proj_id=proj_id,
        project_row_id=project_row_id,
        reporting_period=reporting_period,
        previous_period=previous_period,
        primary_task_id=task.task_id,
        task_code=task.task_code,
        task_name=task.task_name,
        delay_days=slip,
        delay_metric="finish_slip_calendar_days",
        finish_field_used=task.finish_field_used,
        is_critical=task.is_critical,
        total_float_hours=task.total_float_hours,
    )


def incidents_to_records(incidents: list[DetectedIncident]) -> list[dict[str, Any]]:
    return [
        {
            "incident_id": i.incident_id,
            "incident_type": i.incident_type.value,
            "project_row_id": i.project_row_id,
            "proj_id": i.proj_id,
            "reporting_period": i.reporting_period,
            "previous_period": i.previous_period,
            "primary_task_id": i.primary_task_id,
            "task_code": i.task_code,
            "task_name": i.task_name,
            "delay_days": i.delay_days,
            "delay_metric": i.delay_metric,
            "finish_field_used": i.finish_field_used,
            "is_critical": i.is_critical,
            "total_float_hours": i.total_float_hours,
        }
        for i in incidents
    ]
