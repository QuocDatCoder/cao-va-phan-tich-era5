"""Pure helpers for polling, cache invalidation and Vietnam date windows."""
from datetime import date, timedelta
import json
from typing import Any

from frontend.components.controls import local_date_range


ALIASES = {"temperature": "2m_temperature", "precipitation": "total_precipitation",
           "dewpoint": "2m_dewpoint_temperature"}


def matching_states(states: list[dict], params: dict) -> list[dict]:
    variable = ALIASES.get(params["variable"], params["variable"])
    matches = []
    for state in states:
        area = state["area"]
        if (ALIASES.get(state["variable"], state["variable"]) == variable
            and area["south"] <= params["latitude"] <= area["north"]
            and area["west"] <= params["longitude"] <= area["east"]):
            matches.append(state)
    return matches


def latest_synced_date(states: list[dict]) -> str | None:
    dates = [state["last_synced_date"] for state in states if state.get("last_synced_date")]
    return max(dates) if dates else None


def follow_window(params: dict, last_date: str, days: int) -> dict:
    end = date.fromisoformat(last_date)
    start, end_string = local_date_range(end - timedelta(days=days-1), end)
    return {**params, "start": start, "end": end_string}


def completion_events(previous: dict, states: list[dict], job: dict | None = None) -> dict:
    events = dict(previous)
    for state in states:
        if state["status"] in {"completed", "up_to_date"} and state.get("last_synced_date"):
            events[state["sync_id"]] = (
                state.get("completed_at") if state["status"] == "completed" else None
            ) or state["last_synced_date"]
    if job and job["status"] == "completed":
        events["job:" + job["job_id"]] = job.get("completed_at") or job.get("updated_at", "completed")
    return events


def revision_key(provider: str, events: dict[str, Any]) -> str:
    return json.dumps([provider, events], sort_keys=True)
