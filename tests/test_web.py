"""Flask dashboard: current value, timestamp, data status, health, history, charts, logs."""
import pytest

from monitor import db
from monitor.web import create_app
from monitor.worker import Worker


@pytest.fixture
def running(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    for _ in range(60):                   # 6 s of data
        clock.advance(0.1)
        w.step()
    app = create_app(settings, clock=clock)
    return app.test_client(), w


def test_dashboard_shows_current_value_timestamp_status_and_health(running):
    client, w = running
    page = client.get("/").get_data(as_text=True)
    assert "Current temperature" in page and "HEALTHY" in page and "Warm-room alert" in page
    s = client.get("/api/status").get_json()
    assert s["health"]["state"] == "HEALTHY"
    assert s["current"]["is_current"] is True
    assert s["current"]["timestamp"] == s["latest_row"]["timestamp"]
    assert s["latest_row"]["status"] == "OK"
    assert len(s["recent"]) == 12 and s["events"]


def test_stale_value_is_not_presented_as_current(running, clock):
    client, w = running
    clock.advance(5)                      # nothing arrives for 5 s (stale after 1.5 s); heartbeat still recent
    s = client.get("/api/status").get_json()
    assert s["current"]["is_current"] is False
    assert s["health"]["state"] == "DEGRADED" and s["health"]["data_state"] == "STALE"
    assert "STALE, not current" in client.get("/").get_data(as_text=True)


def test_dead_collector_shows_failed(running, clock):
    client, _ = running
    clock.advance(60)                     # collector stopped writing heartbeats
    s = client.get("/healthz").get_json()
    assert s["web"] == "ok" and s["health"] == "FAILED"


def test_history_page_uses_module6_history(running):
    client, _ = running
    page = client.get("/history?dataset=practice&window=day").get_data(as_text=True)
    assert "1420" in page and "1440" in page          # received / expected, Module 6 last-day numbers


def test_chart_breaks_at_gaps(running):
    client, _ = running
    svg = client.get("/chart/practice.svg?window=full&sensor=temperature").get_data(as_text=True)
    assert svg.startswith("<svg") and svg.count("no data") >= 2
    assert svg.count("<polyline") >= 3                 # one line per continuous segment


def test_logs_page_lists_events(running):
    client, _ = running
    page = client.get("/logs").get_data(as_text=True)
    assert "STARTUP" in page and "worker.log" in page


def test_unknown_dataset_is_404(running):
    client, _ = running
    assert client.get("/chart/nope.svg").status_code == 404
