"""Incident detection unit tests."""

from schedule_impact.identify.incidents import detect_incidents


def _task(
    tid: str,
    code: str,
    finish: str,
    float_hrs: str = "40",
    driving: str = "N",
) -> dict[str, str]:
    return {
        "task_id": tid,
        "proj_id": "P1",
        "wbs_id": "W1",
        "task_code": code,
        "task_name": code,
        "status_code": "TK_Active",
        "early_end_date": finish,
        "total_float_hr_cnt": float_hrs,
        "driving_path_flag": driving,
    }


def test_impact_on_critical_slip() -> None:
    prev = [_task("1", "A", "2025-03-01 16:00", "0", "Y")]
    cur = [_task("1", "A", "2025-03-20 16:00", "0", "Y")]
    inc = detect_incidents(cur, prev, programme_id="t", reporting_period="2025-04", previous_period="2025-03")
    assert any(i.incident_type.value == "impact" for i in inc)


def test_float_when_not_critical() -> None:
    prev = [_task("2", "B", "2025-03-01 16:00", "40", "N")]
    cur = [_task("2", "B", "2025-03-20 16:00", "24", "N")]
    inc = detect_incidents(cur, prev, programme_id="t", reporting_period="2025-04", previous_period="2025-03")
    assert any(i.incident_type.value == "float" for i in inc)
