import json


def _parse_sse(lines):
    """Group SSE lines into (event_name, data_string) tuples, split on blank lines."""
    events = []
    name = None
    data = []
    for line in lines:
        if line == "":
            if data:
                events.append((name, "\n".join(data)))
            name, data = None, []
        elif line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        # comment lines (": ping") and other fields are ignored
    if data:
        events.append((name, "\n".join(data)))
    return events


def test_sse_event_payloads_are_valid_json(client):
    upload = client.post(
        "/api/documents/upload",
        files={"file": ("sse.md", b"# sse test", "text/markdown")},
    )
    job_id = upload.json()["job_id"]

    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        events = _parse_sse(response.iter_lines())

    assert events, "stream produced no events"
    assert events[0][0] == "state"

    payloads = [json.loads(data) for _, data in events]  # raises if not JSON
    assert all(isinstance(p, dict) for p in payloads)

    # The stream ends on its own, and the last payload reports a finished job.
    assert payloads[-1].get("status") in ("done", "failed")


def test_sse_unknown_job_returns_404(client):
    assert client.get("/api/jobs/999999/events").status_code == 404