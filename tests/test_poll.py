from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dl_reservation.config import ReservationRequest
from dl_reservation.heartbeat import HEARTBEAT_INTERVAL, HeartbeatState, heartbeat_path_for
from dl_reservation.notifier import HeartbeatPayload
from dl_reservation.poll import _filter_relevant, _months_to_cover, poll_once
from dl_reservation.upstream import Slot


class _RecordNotifier:
    def __init__(self) -> None:
        self.notify_calls: list[list] = []
        self.heartbeat_calls: list[HeartbeatPayload] = []
        self.booked_calls: list = []
        self.booking_failed_calls: list = []
        self.dry_run_calls: list = []

    def notify(self, slots) -> None:
        self.notify_calls.append(list(slots))

    def heartbeat(self, payload) -> None:
        self.heartbeat_calls.append(payload)

    def booked(self, slot, *, confirmation_body=None) -> None:
        self.booked_calls.append((slot, confirmation_body))

    def booking_failed(self, target, reason) -> None:
        self.booking_failed_calls.append((target, reason))

    def dry_run_payload(self, target, payload) -> None:
        self.dry_run_calls.append((target, payload))


def _slot(date_str: str, place: str = "270", course: str = "11") -> Slot:
    return Slot(
        date=date_str, starttime="0800", endtime="0930",
        place=place, course=course,
        capacity=100, reservation=80,
        displaytime="x",
    )


def test_months_to_cover_spans_year_boundary():
    months = _months_to_cover(date(2026, 11, 20), date(2027, 2, 5))
    assert months == ["202611", "202612", "202701", "202702"]


def test_months_to_cover_same_month():
    months = _months_to_cover(date(2026, 5, 8), date(2026, 5, 30))
    assert months == ["202605"]


def test_filter_drops_past_dates_and_off_request_slots():
    request = ReservationRequest(
        candidate_places=("270", "280"),
        candidate_courses=("11",),
        latest_acceptable_date=date(2026, 6, 30),
    )
    today = date(2026, 5, 15)
    now = datetime(2026, 5, 15, 0, 0)  # midnight: today's 08:00 still future
    slots = [
        _slot("20260501"),                       # past
        _slot("20260520"),                       # ok
        _slot("20260620", place="250"),          # wrong place
        _slot("20260620", course="61"),          # wrong course
        _slot("20260720"),                       # past latest_acceptable_date
        _slot("20260620"),                       # ok
    ]
    kept = _filter_relevant(slots, request, today, now=now)
    assert [s.date for s in kept] == ["20260520", "20260620"]


def test_filter_drops_same_day_slots_whose_start_time_already_passed():
    request = ReservationRequest(
        candidate_places=("270",),
        candidate_courses=("11",),
        latest_acceptable_date=date(2026, 5, 31),
    )
    today = date(2026, 5, 8)
    now = datetime(2026, 5, 8, 22, 0)  # user reported 5/8 22:00 incident
    morning = _slot("20260508")  # starttime=0800, already passed
    afternoon = Slot(
        date="20260508", starttime="1100", endtime="1230",
        place="270", course="11",
        capacity=92, reservation=78, displaytime="x",
    )
    tomorrow = _slot("20260509")
    kept = _filter_relevant([morning, afternoon, tomorrow], request, today, now=now)
    assert [s.date for s in kept] == ["20260509"]


def test_silent_baseline_persists_without_notifying(tmp_path: Path):
    request = ReservationRequest(
        candidate_places=("270",),
        candidate_courses=("11",),
        latest_acceptable_date=date(2026, 5, 31),
    )
    fake_open = Slot(
        date="20260520", starttime="0800", endtime="0930",
        place="270", course="11",
        capacity=100, reservation=80, displaytime="x",
    )
    state = tmp_path / "snap.json"
    captured: list = []

    class Capture:
        def notify(self, slots):
            captured.append(list(slots))

    with patch(
        "dl_reservation.poll.fetch_month",
        return_value=[fake_open],
    ):
        new = poll_once(
            request, state, Capture(),
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            silent_baseline=True,
        )

    assert new == [fake_open]      # function still reports what would have fired
    assert captured == []          # but the notifier was not called
    assert state.exists()          # baseline persisted for next run


def test_second_run_after_silent_baseline_emits_only_real_new(tmp_path: Path):
    request = ReservationRequest(
        candidate_places=("270",),
        candidate_courses=("11",),
        latest_acceptable_date=date(2026, 5, 31),
    )
    baseline = Slot(
        date="20260520", starttime="0800", endtime="0930",
        place="270", course="11",
        capacity=100, reservation=80, displaytime="x",
    )
    later = Slot(
        date="20260520", starttime="1100", endtime="1230",
        place="270", course="11",
        capacity=100, reservation=90, displaytime="y",
    )
    state = tmp_path / "snap.json"
    captured: list = []

    class Capture:
        def notify(self, slots):
            captured.append(list(slots))

    with patch("dl_reservation.poll.fetch_month", return_value=[baseline]):
        poll_once(
            request, state, Capture(),
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            silent_baseline=True,
        )
    with patch("dl_reservation.poll.fetch_month", return_value=[baseline, later]):
        poll_once(
            request, state, Capture(),
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            silent_baseline=True,
        )

    assert captured == [[later]]   # only the brand-new slot fires


def _full_slot(date_str: str) -> Slot:
    return Slot(
        date=date_str, starttime="0800", endtime="0930",
        place="270", course="11",
        capacity=10, reservation=10,  # full
        displaytime="x",
    )


def _open_slot(date_str: str) -> Slot:
    return Slot(
        date=date_str, starttime="0800", endtime="0930",
        place="270", course="11",
        capacity=10, reservation=8,  # 2 seats remaining
        displaytime="x",
    )


def _request() -> ReservationRequest:
    return ReservationRequest(
        candidate_places=("270",),
        candidate_courses=("11",),
        latest_acceptable_date=date(2026, 5, 31),
    )


def test_heartbeat_fires_when_window_has_no_open_slots(tmp_path: Path):
    state = tmp_path / "snap.json"
    notifier = _RecordNotifier()
    with patch("dl_reservation.poll.fetch_month",
               return_value=[_full_slot("20260520")]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc),
        )
    assert notifier.notify_calls == [[]]
    assert len(notifier.heartbeat_calls) == 1
    assert notifier.heartbeat_calls[0].deadline == date(2026, 5, 31)
    assert notifier.heartbeat_calls[0].total_slots_in_window == 1
    # state stamped so we don't refire next poll
    hb = HeartbeatState.load(heartbeat_path_for(state))
    assert hb.last_at is not None


def test_heartbeat_suppressed_when_any_slot_open(tmp_path: Path):
    state = tmp_path / "snap.json"
    notifier = _RecordNotifier()
    with patch("dl_reservation.poll.fetch_month",
               return_value=[_full_slot("20260520"), _open_slot("20260521")]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc),
        )
    assert notifier.heartbeat_calls == []


def test_heartbeat_suppressed_within_24h_of_last(tmp_path: Path):
    state = tmp_path / "snap.json"
    last = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
    HeartbeatState(last_at=last).save(heartbeat_path_for(state))

    notifier = _RecordNotifier()
    with patch("dl_reservation.poll.fetch_month",
               return_value=[_full_slot("20260520")]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=last + timedelta(hours=23),
        )
    assert notifier.heartbeat_calls == []


def test_heartbeat_refires_after_24h(tmp_path: Path):
    state = tmp_path / "snap.json"
    last = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
    HeartbeatState(last_at=last).save(heartbeat_path_for(state))

    notifier = _RecordNotifier()
    with patch("dl_reservation.poll.fetch_month",
               return_value=[_full_slot("20260520")]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=last + HEARTBEAT_INTERVAL,
        )
    assert len(notifier.heartbeat_calls) == 1


class _StubBooker:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls: list = []

    def try_book(self, slot):
        self.calls.append(slot)
        from dl_reservation.booker import BookerOutcome, BookerResult
        from dl_reservation.booking_state import BookedSlot
        if self.outcome is BookerOutcome.SUCCESS:
            return BookerResult(
                outcome=BookerOutcome.SUCCESS,
                booked=BookedSlot(
                    date=slot.date, starttime=slot.starttime, endtime=slot.endtime,
                    place=slot.place, course=slot.course,
                    booked_at="2026-05-08T12:00:00+00:00", receipt_no="R-stub",
                ),
                failure_reason=None,
                payload_for_review=None,
                confirmation_body={"code": "A0001", "body": {"receipt_no": "R-stub"}},
            )
        if self.outcome is BookerOutcome.DRY_RUN_ONLY:
            return BookerResult(
                outcome=BookerOutcome.DRY_RUN_ONLY,
                booked=None, failure_reason=None,
                payload_for_review={"date": slot.date},
            )
        return BookerResult(
            outcome=BookerOutcome.PERMANENT_FAILURE,
            booked=None, failure_reason="stub-failure",
            payload_for_review=None,
        )


def test_booker_books_earliest_new_opening(tmp_path: Path):
    from dl_reservation.booker import BookerOutcome
    from dl_reservation.booking_state import load as load_booked, path_for

    state = tmp_path / "snap.json"
    notifier = _RecordNotifier()
    booker = _StubBooker(BookerOutcome.SUCCESS)
    later = Slot(
        date="20260520", starttime="1100", endtime="1230", place="270", course="11",
        capacity=10, reservation=8, displaytime="x",
    )
    earlier = Slot(
        date="20260515", starttime="0800", endtime="0930", place="270", course="11",
        capacity=10, reservation=8, displaytime="x",
    )
    with patch("dl_reservation.poll.fetch_month", return_value=[earlier, later]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            booker=booker,
        )
    # earliest by (date, starttime) wins
    assert len(booker.calls) == 1
    assert booker.calls[0].date == "20260515"
    assert len(notifier.booked_calls) == 1
    # confirmation_body must be threaded to the notifier so it can attach QR.
    booked_slot, conf = notifier.booked_calls[0]
    assert booked_slot.receipt_no == "R-stub"
    assert conf == {"code": "A0001", "body": {"receipt_no": "R-stub"}}
    # BOOKED state persisted for next-poll short-circuit
    assert load_booked(path_for(state)) is not None
    # Full upstream response persisted alongside for QR / audit recovery.
    from dl_reservation.booking_state import confirmation_path_for
    import json as _json
    conf_path = confirmation_path_for(state)
    assert conf_path.exists()
    assert _json.loads(conf_path.read_text(encoding="utf-8")) == conf


def test_booker_short_circuits_when_already_booked(tmp_path: Path):
    from dl_reservation.booker import BookerOutcome
    from dl_reservation.booking_state import BookedSlot, path_for, save as save_booked

    state = tmp_path / "snap.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    save_booked(path_for(state), BookedSlot(
        date="20260515", starttime="0800", endtime="0930", place="270", course="11",
        booked_at="2026-05-08T00:00:00+00:00", receipt_no="prior",
    ))
    notifier = _RecordNotifier()
    booker = _StubBooker(BookerOutcome.SUCCESS)
    new_slot = Slot(
        date="20260516", starttime="0800", endtime="0930", place="270", course="11",
        capacity=10, reservation=8, displaytime="x",
    )
    with patch("dl_reservation.poll.fetch_month", return_value=[new_slot]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            booker=booker,
        )
    assert booker.calls == []
    assert notifier.booked_calls == []


def test_notify_and_heartbeat_suppressed_when_already_booked(tmp_path: Path):
    """Once user holds a booking, new-slot alerts are noise — silence them."""
    from dl_reservation.booking_state import BookedSlot, path_for, save as save_booked

    state = tmp_path / "snap.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    save_booked(path_for(state), BookedSlot(
        date="20260515", starttime="0800", endtime="0930", place="270", course="11",
        booked_at="2026-05-08T00:00:00+00:00", receipt_no="prior",
    ))
    notifier = _RecordNotifier()
    new_slot = Slot(
        date="20260516", starttime="0800", endtime="0930", place="270", course="11",
        capacity=10, reservation=8, displaytime="x",
    )
    with patch("dl_reservation.poll.fetch_month", return_value=[new_slot]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
        )
    # New slot exists, but BOOKED state means user already holds one → silence.
    assert notifier.notify_calls == []
    assert notifier.heartbeat_calls == []


def test_booker_dry_run_routes_payload_to_notifier(tmp_path: Path):
    from dl_reservation.booker import BookerOutcome

    state = tmp_path / "snap.json"
    notifier = _RecordNotifier()
    booker = _StubBooker(BookerOutcome.DRY_RUN_ONLY)
    slot = Slot(
        date="20260515", starttime="0800", endtime="0930", place="270", course="11",
        capacity=10, reservation=8, displaytime="x",
    )
    with patch("dl_reservation.poll.fetch_month", return_value=[slot]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            booker=booker,
        )
    assert len(notifier.dry_run_calls) == 1
    assert notifier.booked_calls == []


def test_booker_failure_routes_to_notifier_failed_path(tmp_path: Path):
    from dl_reservation.booker import BookerOutcome

    state = tmp_path / "snap.json"
    notifier = _RecordNotifier()
    booker = _StubBooker(BookerOutcome.PERMANENT_FAILURE)
    slot = Slot(
        date="20260515", starttime="0800", endtime="0930", place="270", course="11",
        capacity=10, reservation=8, displaytime="x",
    )
    with patch("dl_reservation.poll.fetch_month", return_value=[slot]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            now=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
            booker=booker,
        )
    assert len(notifier.booking_failed_calls) == 1
    assert notifier.booked_calls == []


def test_heartbeat_suppressed_during_silent_baseline(tmp_path: Path):
    state = tmp_path / "snap.json"
    notifier = _RecordNotifier()
    with patch("dl_reservation.poll.fetch_month",
               return_value=[_full_slot("20260520")]):
        poll_once(
            _request(), state, notifier,
            today=date(2026, 5, 8),
            silent_baseline=True,
            now=datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc),
        )
    assert notifier.notify_calls == []
    assert notifier.heartbeat_calls == []
