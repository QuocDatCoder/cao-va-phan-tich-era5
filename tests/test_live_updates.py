from frontend.live_updates import completion_events, follow_window, matching_states


def state(**changes):
    value = {"sync_id": "s1", "variable": "2m_temperature", "status": "completed",
             "area": {"north": 11, "south": 10, "west": 106, "east": 107},
             "last_synced_date": "2025-02-01", "completed_at": "2025-02-06T01:00:00Z"}
    return {**value, **changes}


def test_matching_requires_variable_alias_and_containing_region():
    params = {"variable": "temperature", "latitude": 10.8, "longitude": 106.6}
    assert matching_states([state(), state(variable="total_precipitation")], params) == [state()]
    assert matching_states([state()], {**params, "latitude": 12}) == []


def test_follow_window_uses_complete_vietnam_days_across_month_boundary():
    result = follow_window({"variable": "temperature"}, "2025-02-01", 2)
    assert result["start"] == "2025-01-31T00:00:00+07:00"
    assert result["end"] == "2025-02-01T23:00:00+07:00"


def test_running_or_failed_state_does_not_invalidate_previous_success():
    previous = completion_events({}, [state()])
    assert completion_events(previous, [state(status="running", completed_at=None)]) == previous
    assert completion_events(previous, [state(status="failed")]) == previous
    changed = completion_events(previous, [state(completed_at="2025-02-07T01:00:00Z")])
    assert changed != previous


def test_up_to_date_catches_completion_missed_between_polls():
    assert completion_events({}, [state(status="up_to_date")]) == {"s1": "2025-02-01"}


def test_download_completion_also_invalidates_cache_once():
    job = {"job_id": "j1", "status": "completed", "completed_at": "2025-02-06T01:00:00Z"}
    events = completion_events({}, [], job)
    assert events == {"job:j1": job["completed_at"]}
    assert completion_events(events, [], job) == events
