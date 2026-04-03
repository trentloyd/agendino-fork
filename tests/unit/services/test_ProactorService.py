"""Unit tests for ProactorService."""

import pytest

from services.ProactorService import ProactorService


@pytest.fixture
def service():
    return ProactorService()


def _ev(id, title, start, end, all_day=False):
    return {
        "id": id,
        "title": title,
        "start_at": start,
        "end_at": end,
        "is_all_day": all_day,
    }


class TestDetectOverlaps:
    def test_no_events(self, service):
        assert service.detect_overlaps([]) == []

    def test_no_overlap(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
            _ev(2, "B", "2026-04-01 10:00:00", "2026-04-01 11:00:00"),
        ]
        assert service.detect_overlaps(events) == []

    def test_overlap_detected(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:30:00"),
            _ev(2, "B", "2026-04-01 10:00:00", "2026-04-01 11:00:00"),
        ]
        result = service.detect_overlaps(events)
        assert len(result) == 1
        assert result[0]["event_a"]["id"] == 1
        assert result[0]["event_b"]["id"] == 2
        assert result[0]["overlap_minutes"] == 30.0
        assert result[0]["severity"] == "high"

    def test_overlap_includes_calendar_name(self, service):
        events = [
            {
                **_ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:30:00"),
                "calendar_name": "Work",
                "calendar_color": "#ff0000",
            },
            {
                **_ev(2, "B", "2026-04-01 10:00:00", "2026-04-01 11:00:00"),
                "calendar_name": "Personal",
                "calendar_color": "#00ff00",
            },
        ]
        result = service.detect_overlaps(events)
        assert len(result) == 1
        assert result[0]["event_a"]["calendar_name"] == "Work"
        assert result[0]["event_b"]["calendar_name"] == "Personal"
        assert result[0]["event_a"]["calendar_color"] == "#ff0000"

    def test_all_day_events_skipped(self, service):
        events = [
            _ev(1, "Holiday", "2026-04-01 00:00:00", "2026-04-02 00:00:00", all_day=True),
            _ev(2, "Meeting", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
        ]
        assert service.detect_overlaps(events) == []

    def test_severity_levels(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 09:08:00"),
            _ev(2, "B", "2026-04-01 09:05:00", "2026-04-01 09:10:00"),  # 3 min overlap → low
        ]
        result = service.detect_overlaps(events)
        assert result[0]["severity"] == "low"

    def test_medium_severity(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 09:20:00"),
            _ev(2, "B", "2026-04-01 09:05:00", "2026-04-01 09:30:00"),  # 15 min overlap → medium
        ]
        result = service.detect_overlaps(events)
        assert result[0]["severity"] == "medium"


class TestDetectBackToBack:
    def test_no_back_to_back(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
            _ev(2, "B", "2026-04-01 10:30:00", "2026-04-01 11:00:00"),
        ]
        assert service.detect_back_to_back(events) == []

    def test_back_to_back_detected(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
            _ev(2, "B", "2026-04-01 10:02:00", "2026-04-01 11:00:00"),
        ]
        result = service.detect_back_to_back(events)
        assert len(result) == 1
        assert result[0]["gap_minutes"] == 2.0


class TestDetectGaps:
    def test_gap_between_events(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
            _ev(2, "B", "2026-04-01 14:00:00", "2026-04-01 15:00:00"),
        ]
        gaps = service.detect_gaps(events)
        # Should have: before A (08:00-09:00), between A and B (10:00-14:00), after B (15:00-18:00)
        assert len(gaps) >= 2
        kinds = [g["kind"] for g in gaps]
        assert "idle_window" in kinds or "available" in kinds

    def test_no_events_full_day_gap(self, service):
        gaps = service.detect_gaps([])
        assert gaps == []


class TestAssessDayLoad:
    def test_normal_day(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
            _ev(2, "B", "2026-04-01 14:00:00", "2026-04-01 15:00:00"),
        ]
        result = service.assess_day_load(events)
        assert len(result) == 1
        assert result[0]["total_hours"] == 2.0
        assert result[0]["overloaded"] is False

    def test_overloaded_day(self, service):
        events = [
            _ev(1, "A", "2026-04-01 08:00:00", "2026-04-01 12:00:00"),
            _ev(2, "B", "2026-04-01 13:00:00", "2026-04-01 16:00:00"),
        ]
        result = service.assess_day_load(events)
        assert result[0]["total_hours"] == 7.0
        assert result[0]["overloaded"] is True


class TestAnalyzeRange:
    def test_healthy_schedule(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
            _ev(2, "B", "2026-04-01 11:00:00", "2026-04-01 12:00:00"),
        ]
        result = service.analyze_range(events)
        assert result["summary"]["health"] == "good"
        assert result["summary"]["overlap_count"] == 0
        assert result["summary"]["back_to_back_count"] == 0
        assert "day_timelines" in result

    def test_problematic_schedule(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:30:00"),
            _ev(2, "B", "2026-04-01 10:00:00", "2026-04-01 11:00:00"),
            _ev(3, "C", "2026-04-01 11:02:00", "2026-04-01 12:00:00"),
            _ev(4, "D", "2026-04-01 13:00:00", "2026-04-01 17:00:00"),
            _ev(5, "E", "2026-04-01 17:01:00", "2026-04-01 18:00:00"),
        ]
        result = service.analyze_range(events)
        assert result["summary"]["overlap_count"] >= 1
        assert result["summary"]["back_to_back_count"] >= 1
        assert result["summary"]["health"] in ("fair", "poor")


class TestBuildDayTimelines:
    def test_empty_events(self, service):
        assert service.build_day_timelines([]) == []

    def test_single_meeting_with_gaps(self, service):
        events = [
            _ev(1, "Standup", "2026-04-01 10:00:00", "2026-04-01 10:30:00"),
        ]
        result = service.build_day_timelines(events)
        assert len(result) == 1
        day = result[0]
        assert day["date"] == "2026-04-01"

        # Segments: free (08:00-10:00) + meeting (10:00-10:30) + free (10:30-18:00)
        assert len(day["segments"]) == 3
        assert day["segments"][0]["type"] == "free"
        assert day["segments"][1]["type"] == "meeting"
        assert day["segments"][1]["title"] == "Standup"
        assert day["segments"][2]["type"] == "free"

        # Percentages should sum close to 100
        total_pct = sum(s["pct"] for s in day["segments"])
        assert 99.5 <= total_pct <= 100.5

    def test_totals(self, service):
        events = [
            _ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 11:00:00"),
            _ev(2, "B", "2026-04-01 14:00:00", "2026-04-01 15:00:00"),
        ]
        result = service.build_day_timelines(events)
        day = result[0]
        assert day["total_busy_min"] == 180.0  # 2h + 1h
        assert day["total_free_min"] == 1260.0  # 24h - 3h = 21h = 1260min (default 0:00–24:00 window)

    def test_segments_have_calendar_info(self, service):
        events = [
            {
                **_ev(1, "A", "2026-04-01 09:00:00", "2026-04-01 10:00:00"),
                "calendar_name": "Work",
                "calendar_color": "#ff0000",
            },
        ]
        result = service.build_day_timelines(events)
        meeting_seg = [s for s in result[0]["segments"] if s["type"] == "meeting"][0]
        assert meeting_seg["calendar_name"] == "Work"
        assert meeting_seg["calendar_color"] == "#ff0000"

    def test_all_day_events_excluded(self, service):
        events = [
            _ev(1, "Holiday", "2026-04-01 00:00:00", "2026-04-02 00:00:00", all_day=True),
        ]
        assert service.build_day_timelines(events) == []
