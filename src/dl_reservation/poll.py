"""Single poll run: fetch → diff → notify → persist.

Entry point for cron / launchd. Run via `dl-poll` (project script) or
`python -m dl_reservation.poll`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

from . import booking_state
from . import credentials as credentials_module
from . import heartbeat as heartbeat_state
from . import snapshot
from .booker import Booker, BookerOutcome, cancel_booking
from .config import ReservationRequest, load_from_file
from .notifier import (
    BarkNotifier,
    EmailNotifier,
    HeartbeatPayload,
    Notifier,
    StdoutNotifier,
    TeeNotifier,
)
from .upstream import Slot, fetch_month


_log = logging.getLogger("dl_reservation.poll")


def _months_to_cover(today: date, latest: date) -> list[str]:
    """Return YYYYMM strings from `today`'s month through `latest`'s month."""
    months: list[str] = []
    cursor = date(today.year, today.month, 1)
    end = date(latest.year, latest.month, 1)
    while cursor <= end:
        months.append(f"{cursor.year:04d}{cursor.month:02d}")
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _filter_relevant(
    slots: list[Slot],
    request: ReservationRequest,
    today: date,
    *,
    now: datetime | None = None,
) -> list[Slot]:
    """Drop slots whose start time has already passed and any slot outside
    the request's interest.

    Compares full `start_datetime`, not just date — at 22:00 today, a slot
    at 11:00 today is unbookable and must not be emailed. (Reported by the
    user on 2026-05-08: same-day past-time slots showed up as "new
    openings" because the upstream stops decrementing capacity once the
    slot ends.)
    """
    now = now or datetime.now()
    relevant: list[Slot] = []
    for slot in slots:
        if slot.start_datetime <= now:
            continue
        if not request.matches(slot.place, slot.course, slot.date_obj):
            continue
        relevant.append(slot)
    return relevant


def _build_default_notifier() -> Notifier:
    """Stdout always; add Email if its env vars are configured."""
    children: list[Notifier] = [StdoutNotifier()]
    if all(os.environ.get(var) for var in (
        EmailNotifier.ENV_USER,
        EmailNotifier.ENV_PASSWORD,
        EmailNotifier.ENV_TO,
    )):
        children.append(EmailNotifier.from_env())
        _log.info("email notifier enabled (to=%s)", os.environ[EmailNotifier.ENV_TO])
    else:
        _log.info("email notifier disabled — required env vars not set")
    if os.environ.get(BarkNotifier.ENV_URL):
        children.append(BarkNotifier.from_env())
        _log.info("bark notifier enabled")
    return children[0] if len(children) == 1 else TeeNotifier(*children)


def poll_once(
    request: ReservationRequest,
    state_path: Path,
    notifier: Notifier,
    *,
    today: date | None = None,
    client: httpx.Client | None = None,
    silent_baseline: bool = False,
    now: datetime | None = None,
    booker: Booker | None = None,
) -> list[Slot]:
    """Run the poll-fetch-diff-notify cycle once. Returns slots that would
    have been notified — if `silent_baseline` is true and no prior snapshot
    existed, the notifier is intentionally not invoked but the snapshot is
    still persisted, so the next run starts diffing from a real baseline.

    Heartbeat: if the deadline window contains zero bookable slots and the
    last heartbeat was sent more than HEARTBEAT_INTERVAL ago, fire a
    heartbeat email (suppressed during silent_baseline first runs).
    """
    today = today or date.today()
    now = now or heartbeat_state.utc_now()
    months = _months_to_cover(today, request.latest_acceptable_date)
    _log.info(
        "polling places=%s courses=%s months=%s",
        list(request.candidate_places),
        list(request.candidate_courses),
        months,
    )

    fetched: list[Slot] = []
    for place in request.candidate_places:
        for course in request.candidate_courses:
            for yyyymm in months:
                fetched.extend(fetch_month(place, course, yyyymm, client=client))

    # `now` is aware UTC (heartbeat clock); Slot.start_datetime is naive JST.
    local_now = now.astimezone().replace(tzinfo=None)
    relevant = _filter_relevant(fetched, request, today, now=local_now)
    prev = snapshot.load(state_path)
    is_first_run = not prev
    new_openings = snapshot.diff_new_openings(prev, relevant)

    # Once the user holds a booking, "new slot" alerts become noise — they
    # already have what they wanted. Suppress notify + heartbeat + booker;
    # snapshot is still persisted so a `--reset-booking` user starts from
    # a fresh baseline rather than re-flagging every existing opening.
    already_booked = booking_state.load(booking_state.path_for(state_path)) is not None

    if silent_baseline and is_first_run:
        _log.info(
            "silent baseline: persisting %d slot(s) without notifying "
            "(would have flagged %d as new)",
            len(relevant), len(new_openings),
        )
        suppress_notify = True
    elif already_booked:
        if new_openings:
            _log.info(
                "BOOKED — suppressing %d new-opening alert(s); user already "
                "holds a slot, no further action needed",
                len(new_openings),
            )
        suppress_notify = True
    else:
        notifier.notify(new_openings)
        suppress_notify = False

    snapshot.save(state_path, relevant)

    if not suppress_notify:
        _maybe_send_heartbeat(request, relevant, state_path, notifier, now)
        if booker is not None and new_openings:
            _maybe_book(booker, new_openings, state_path, notifier)

    return new_openings


def _pick_target_slot(new_openings: list[Slot]) -> Slot:
    """Pick the slot to book when multiple appear in one poll.

    v1 default: earliest by (date, starttime). The user wants the earliest
    feasible exam, and our deadline filter has already removed anything
    past `latest_acceptable_date`.
    """
    return min(new_openings, key=lambda s: (s.date, s.starttime))


def _maybe_book(
    booker: Booker,
    new_openings: list[Slot],
    state_path: Path,
    notifier: Notifier,
) -> None:
    """ADR-6 single-shot: short-circuit if already BOOKED, else attempt."""
    booked_path = booking_state.path_for(state_path)
    if booking_state.load(booked_path) is not None:
        _log.info("booker short-circuited — already in BOOKED state")
        return

    target = _pick_target_slot(new_openings)
    _log.info("booker target: %s %s %s/%s",
              target.date, target.starttime, target.place, target.course)

    result = booker.try_book(target)

    if result.outcome is BookerOutcome.SUCCESS:
        assert result.booked is not None
        booking_state.save(booked_path, result.booked)
        if result.confirmation_body is not None:
            booking_state.save_confirmation(
                booking_state.confirmation_path_for(state_path),
                result.confirmation_body,
            )
        notifier.booked(result.booked, confirmation_body=result.confirmation_body)
        _log.info("BOOKED: %s receipt_no=%s",
                  result.booked.date, result.booked.receipt_no)
    elif result.outcome is BookerOutcome.PERMANENT_FAILURE:
        notifier.booking_failed(target, result.failure_reason or "unknown")
        _log.warning("booker permanent failure: %s", result.failure_reason)
    elif result.outcome is BookerOutcome.DRY_RUN_ONLY:
        assert result.payload_for_review is not None
        notifier.dry_run_payload(target, result.payload_for_review)
        _log.info("booker dry-run: payload routed to user for review")


def _maybe_send_heartbeat(
    request: ReservationRequest,
    relevant: list[Slot],
    state_path: Path,
    notifier: Notifier,
    now: datetime,
) -> None:
    """Fire a heartbeat email if the window has zero bookable slots and
    we have not sent one in the last HEARTBEAT_INTERVAL.
    """
    if any(s.is_open for s in relevant):
        return  # there is something bookable; immediate alerts cover this

    hb_path = heartbeat_state.heartbeat_path_for(state_path)
    state = heartbeat_state.HeartbeatState.load(hb_path)
    if not state.should_send(now):
        return

    notifier.heartbeat(
        HeartbeatPayload(
            deadline=request.latest_acceptable_date,
            place_count=len(request.candidate_places),
            course_count=len(request.candidate_courses),
            total_slots_in_window=len(relevant),
            last_poll_at=now.isoformat(),
        )
    )
    heartbeat_state.HeartbeatState(last_at=now).save(hb_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dl-poll", description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="JSON file with candidate_places / candidate_courses / latest_acceptable_date",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("state/snapshot.json"),
        help="Snapshot file (default: state/snapshot.json)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (default: INFO)",
    )
    parser.add_argument(
        "--silent-baseline",
        action="store_true",
        help=(
            "If no prior snapshot exists, persist current openings as the "
            "baseline without sending notifications. Use on first run before "
            "wiring into cron to avoid being flooded with every currently-open slot."
        ),
    )
    parser.add_argument(
        "--enable-booker",
        action="store_true",
        help=(
            "Activate the v1 single-shot booker. Reads credentials from "
            "macOS Keychain (run `dl-credentials set` first). Default mode "
            "is dry-run — combine with --book-real to actually send putres."
        ),
    )
    parser.add_argument(
        "--book-real",
        action="store_true",
        help=(
            "Disable dry-run. Only meaningful with --enable-booker. Per "
            "ADR-5 §2, run --enable-booker WITHOUT this first to inspect "
            "the payload before unlocking real submission."
        ),
    )
    parser.add_argument(
        "--reset-booking",
        action="store_true",
        help=(
            "Clear LOCAL BOOKED state (state/booked.json) and exit. "
            "Use after manually cancelling on the website if you want the "
            "booker to start watching for slots again. Does NOT call the "
            "upstream cancel API — for that, use --cancel-booking."
        ),
    )
    parser.add_argument(
        "--cancel-booking",
        action="store_true",
        help=(
            "Cancel the held booking via upstream /cancel API and clear "
            "LOCAL state. Reads booker credentials from .env.local (same "
            "as --enable-booker). User-initiated only — booker never auto-"
            "cancels (per ADR-6 single-shot)."
        ),
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help=(
            "Send one FAKE opening through every configured notifier "
            "(stdout / email / Bark) and exit. Verifies env-var wiring "
            "before trusting cron. No upstream request is made."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    if args.reset_booking:
        booked_path = booking_state.path_for(args.state)
        booking_state.clear(booked_path)
        _log.info("BOOKED state cleared (%s); booker will resume watching", booked_path)
        return 0

    if args.cancel_booking:
        return _cancel_booking_flow(args)

    if args.test_notify:
        return _test_notify_flow()

    request = load_from_file(args.config)
    notifier = _build_default_notifier()
    booker = _build_booker(args) if args.enable_booker else None

    poll_once(
        request, args.state, notifier,
        silent_baseline=args.silent_baseline,
        booker=booker,
    )
    return 0


# ponytail: clearly-fake slot (year 2099, displaytime says TEST) so a stray
# run can never be mistaken for a real opening.
_TEST_SLOT = Slot(
    date="20991231", starttime="0900", endtime="1030",
    place="270", course="11", capacity=1, reservation=0,
    displaytime="【TEST】通知テスト",
)


def _test_notify_flow() -> int:
    notifier = _build_default_notifier()
    _log.info("sending test notification via %s", type(notifier).__name__)
    notifier.notify([_TEST_SLOT])
    _log.info("test notification sent — check your inbox / phone")
    return 0


def cancel_held_booking(state: Path) -> tuple[bool, str]:
    """Cancel the held booking via upstream + clear local state.

    Shared by `--cancel-booking` and the one-tap cancel server. Returns
    (ok, human-readable message); never raises.
    """
    booked_path = booking_state.path_for(state)
    booked = booking_state.load(booked_path)
    if booked is None:
        return False, f"no BOOKED state at {booked_path} — nothing to cancel"
    try:
        creds = credentials_module.load()
    except credentials_module.CredentialsNotFound as e:
        return False, f"cancel aborted — {e}"
    _log.info("cancelling booking %s %s %s/%s",
              booked.date, booked.starttime, booked.place, booked.course)
    result = cancel_booking(booked, creds)
    if not result.success:
        return False, f"cancel failed (state file kept): {result.failure_reason}"
    booking_state.clear(booked_path)
    return True, (f"cancelled {booked.date} {booked.starttime}-{booked.endtime}; "
                  "local BOOKED state cleared")


def _cancel_booking_flow(args: argparse.Namespace) -> int:
    """User-initiated cancel: API call + local state clear. Exit code != 0 on failure."""
    ok, msg = cancel_held_booking(args.state)
    (_log.info if ok else _log.error)(msg)
    return 0 if ok else 1


def _build_booker(args: argparse.Namespace) -> Booker | None:
    """Read Keychain creds and instantiate the booker. Returns None if no creds."""
    try:
        creds = credentials_module.load()
    except credentials_module.CredentialsNotFound as e:
        _log.error("booker disabled — %s", e)
        return None
    dry_run = not args.book_real
    _log.info("booker enabled (dry_run=%s)", dry_run)
    return Booker(creds, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
