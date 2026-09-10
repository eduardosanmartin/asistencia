"""Hours & lateness computation tests (tasks 5.1, 5.2).

Covers: chronological greedy pairing, on-time / within-grace / beyond-grace
handling, orphan exits, double-exit flagging, open entries, extra-shift
recognition (OK_extra), and daily/weekly/monthly subtotals.
"""

from datetime import date, datetime, timezone

from app.models import AttendanceEvent, SystemConfig, User
from app.services.hours import compute_hours
from app.services.security import hash_password

from tests.helpers import create_user


def _user(username="obrero"):
    return create_user(role="funcionario", username=username,
                       name=f"Usuario {username}")


def _mark(user, event_type, outcome, ts, delay=0):
    return AttendanceEvent.create(
        user=user, event_type=event_type, source="qr", outcome=outcome,
        timestamp=ts, delay_minutes=delay,
    )


def _at(hour, minute, day=9):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


# ── Greedy pairing chains (task 5.1) ──────────────────────────────────


class TestComputeHours:
    def test_pairs_entry_exit_chain(self, app):
        worker = _user()
        entry = _mark(worker, "entry", "OK", _at(6, 0, 7))
        exit_ = _mark(worker, "exit", "OK", _at(14, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        assert len(report["pairs"]) == 1
        pair = report["pairs"][0]
        assert pair["entry"].id == entry.id
        assert pair["exit"].id == exit_.id
        assert pair["minutes"] == 480
        assert pair["is_open"] is False
        assert report["totals"] == {"minutes": 480, "extra_minutes": 0,
                                    "days_worked": 1}

    def test_on_time_entry_zero_delay(self, app):
        worker = _user(username="puntual")
        _mark(worker, "entry", "OK", _at(6, 0, 7), delay=0)
        _mark(worker, "exit", "OK", _at(14, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))
        assert report["pairs"][0]["delay_minutes"] == 0
        assert report["pairs"][0]["late"] is False

    def test_within_grace_no_deduction(self, app):
        worker = _user(username="tolerancia")
        _mark(worker, "entry", "OK", _at(6, 4, 7), delay=4)  # grace = 5
        _mark(worker, "exit", "OK", _at(14, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))
        pair = report["pairs"][0]
        assert pair["delay_minutes"] == 4
        assert pair["late"] is False          # within grace → not flagged
        assert pair["minutes"] == 476          # full minutes, no deduction

    def test_beyond_grace_flagged(self, app):
        worker = _user(username="atrasado")
        _mark(worker, "entry", "OK", _at(6, 6, 7), delay=6)  # grace = 5
        _mark(worker, "exit", "OK", _at(14, 6, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))
        pair = report["pairs"][0]
        assert pair["late"] is True
        assert len(report["flags"]["late_entries"]) == 1

    def test_orphan_exit_and_double_exit_flag(self, app):
        worker = _user(username="huérfano")
        _mark(worker, "exit", "OK", _at(8, 0, 7))     # exit without entry
        _mark(worker, "exit", "OK", _at(9, 0, 7))     # second exit in a row

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        assert len(report["pairs"]) == 0
        assert len(report["flags"]["orphan_exits"]) == 2
        assert len(report["flags"]["double_exits"]) == 1
        assert report["totals"]["minutes"] == 0

    def test_open_entry_at_range_end(self, app):
        worker = _user(username="abierto")
        entry = _mark(worker, "entry", "OK", _at(9, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        pair = report["pairs"][0]
        assert pair["entry"].id == entry.id
        assert pair["exit"] is None
        assert pair["is_open"] is True
        assert pair["minutes"] == 0

    def test_extra_shift_pair_is_flagged_extra(self, app):
        worker = _user(username="extra")
        _mark(worker, "entry", "OK_extra", _at(16, 0, 7), delay=0)
        _mark(worker, "exit", "OK_extra", _at(17, 30, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        pair = report["pairs"][0]
        assert pair["is_extra"] is True
        assert pair["minutes"] == 90
        assert report["totals"]["extra_minutes"] == 90
        assert report["totals"]["minutes"] == 90

    def test_invalid_attempts_excluded_from_pairs(self, app):
        worker = _user(username="invalido")
        _mark(worker, "entry", "OK", _at(6, 0, 7))
        _mark(worker, "entry", "INVALIDO_reuso", _at(6, 1, 7))
        _mark(worker, "exit", "INVALIDO_qr_expirado", _at(6, 2, 7))
        _mark(worker, "exit", "OK", _at(14, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        assert len(report["pairs"]) == 1
        assert report["pairs"][0]["minutes"] == 480

    def test_daily_weekly_monthly_subtotals(self, app):
        worker = _user(username="subtotales")
        # Mon 2026-09-07: 480
        _mark(worker, "entry", "OK", _at(6, 0, 7))
        _mark(worker, "exit", "OK", _at(14, 0, 7))
        # Tue 2026-09-08: 476 (entry 06:04, grace)
        _mark(worker, "entry", "OK", _at(6, 4, 8), delay=4)
        _mark(worker, "exit", "OK", _at(14, 0, 8))
        # Wed 2026-09-09: 480, late (06:06)
        _mark(worker, "entry", "OK", _at(6, 6, 9), delay=6)
        _mark(worker, "exit", "OK", _at(14, 6, 9))
        # Thu 2026-09-10: open entry → 0
        _mark(worker, "entry", "OK", _at(9, 0, 10))
        # Fri 2026-09-11: extra 90
        _mark(worker, "entry", "OK_extra", _at(16, 0, 11))
        _mark(worker, "exit", "OK_extra", _at(17, 30, 11))

        report = compute_hours(worker.id, date(2026, 9, 7),
                               date(2026, 9, 13))

        assert report["totals"]["minutes"] == 1526
        assert report["totals"]["extra_minutes"] == 90
        assert report["totals"]["days_worked"] == 5
        assert report["subtotals"]["daily"] == {
            "2026-09-07": 480,
            "2026-09-08": 476,
            "2026-09-09": 480,
            "2026-09-10": 0,
            "2026-09-11": 90,
        }
        assert report["subtotals"]["weekly"] == {"2026-W37": 1526}
        assert report["subtotals"]["monthly"] == {"2026-09": 1526}

    def test_range_filters_outside_events(self, app):
        worker = _user(username="rango")
        _mark(worker, "entry", "OK", _at(6, 0, 5))    # Sep 5: outside
        _mark(worker, "exit", "OK", _at(14, 0, 5))
        _mark(worker, "entry", "OK", _at(6, 0, 8))    # Sep 8: inside
        _mark(worker, "exit", "OK", _at(14, 0, 8))
        _mark(worker, "entry", "OK", _at(6, 0, 14))   # Sep 14: outside
        _mark(worker, "exit", "OK", _at(14, 0, 14))

        report = compute_hours(worker.id, date(2026, 9, 7),
                               date(2026, 9, 13))
        assert report["totals"]["minutes"] == 480
        assert report["totals"]["days_worked"] == 1

    def test_consecutive_entries_close_first_pair(self, app):
        """Entry → entry → exit: first pair has is_open=False, minutes=0;
        second pair closes normally. flags['consecutive_entries'] tracks
        the first entry."""
        worker = _user(username="consecutivo")
        entry1 = _mark(worker, "entry", "OK", _at(6, 0, 7))
        entry2 = _mark(worker, "entry", "OK", _at(8, 0, 7))
        exit_ = _mark(worker, "exit", "OK", _at(14, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        assert len(report["pairs"]) == 2
        # First pair: closed by consecutive entry, 0 minutes, not open
        first_pair = report["pairs"][0]
        assert first_pair["entry"].id == entry1.id
        assert first_pair["exit"] is None
        assert first_pair["is_open"] is False
        assert first_pair["minutes"] == 0
        # Second pair: normal entry→exit with real minutes
        second_pair = report["pairs"][1]
        assert second_pair["entry"].id == entry2.id
        assert second_pair["exit"].id == exit_.id
        assert second_pair["is_open"] is False
        assert second_pair["minutes"] == 360
        # consecutive_entries flag tracks the first entry
        assert len(report["flags"]["consecutive_entries"]) == 1
        assert report["flags"]["consecutive_entries"][0].id == entry1.id

    def test_late_entry_closed_by_consecutive_flagged_late(self, app):
        """Late entry (delay > grace) → consecutive entry → exit:
        first pair should appear in flags['late_entries']."""
        worker = _user(username="late_consec")
        # Late entry at 06:06 (delay=6, grace=5)
        entry1 = _mark(worker, "entry", "OK", _at(6, 6, 7), delay=6)
        entry2 = _mark(worker, "entry", "OK", _at(8, 0, 7))
        exit_ = _mark(worker, "exit", "OK", _at(14, 0, 7))

        report = compute_hours(worker.id, date(2026, 9, 7), date(2026, 9, 7))

        assert len(report["pairs"]) == 2
        first_pair = report["pairs"][0]
        assert first_pair["late"] is True
        # First pair must appear in late_entries
        assert len(report["flags"]["late_entries"]) == 1
        assert report["flags"]["late_entries"][0]["entry"].id == entry1.id