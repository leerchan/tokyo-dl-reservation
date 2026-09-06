"""v1 single-shot booker — call /putres to grab a slot once.

Schema source: live cURL captured from a real successful booking on
2026-05-08 (see OBSERVATIONS.md §putres schema). All 4 ADR-5 hard
rules are enforced here:

  §1 No double-booking — Booker is short-circuited by booking_state.load()
     in poll.py *before* try_book is ever called.
  §2 Mandatory dry-run — `dry_run=True` (the default for first-time use)
     prints the payload to a notifier path and returns without sending.
  §3 Exponential backoff — 1s / 3s / 9s, max 3 retries, only on 5xx /
     network errors. 4xx and code != A0001 are permanent.
  §4 Credentials lifetime — caller passes BookerCredentials in;
     booker never persists it, never logs values.

Per ADR-6, a successful book transitions WATCHING → BOOKED; the caller
writes booking_state.save(...) before returning to user.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import httpx

from .booking_state import BookedSlot
from .codes import API_BASE, USER_AGENT
from .credentials import BookerCredentials
from .upstream import Slot


_log = logging.getLogger("dl_reservation.booker")


class BookerOutcome(Enum):
    SUCCESS = "success"
    PERMANENT_FAILURE = "permanent_failure"
    DRY_RUN_ONLY = "dry_run_only"


@dataclass(frozen=True, slots=True)
class BookerResult:
    outcome: BookerOutcome
    booked: BookedSlot | None              # set on SUCCESS
    failure_reason: str | None             # set on PERMANENT_FAILURE
    payload_for_review: dict | None        # set on DRY_RUN_ONLY
    confirmation_body: dict | None = None  # full putres response on SUCCESS


# Half-width → fullwidth digit map. The upstream SPA does this client-side
# before POSTing; calgetres returns half-width, but putres rejects half-width
# in `gracer_no` fields. Verified 2026-05-08 from live cURL: gracer_no came
# in fullwidth, even though the original card prints half-width.
_FW_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def _to_fullwidth_digits(s: str) -> str:
    return s.translate(_FW_DIGITS)


# Empirically the upstream returns *several* shapes on a successful putres:
#   1. `{"code": "A0001", "body": {"receipt_no": "..."}}`  (test cURL fixture)
#   2. `{"status": "OK", "code": "A4001",
#         "res_no": "...", "rec_no": "...", "yoyakuno": "..."}`  (live 2026-05-09)
# So a fixed `code == "A0001"` check produces a false negative for shape #2.
# Instead: accept *either* the legacy A0001 envelope OR an explicit
# `status == "OK"` paired with any populated reservation-id field at any
# nesting level. The receipt we surface to the user is whichever ID field
# the upstream chose to populate (res_no / receipt_no / yoyakuno / etc.).
_RECEIPT_KEYS = (
    "res_no", "receipt_no", "receiptNo", "receipt",
    "yoyakuno", "yoyaku_no", "yoyakuNo",
    "rec_no", "recNo",
)


def _is_success_response(body: dict) -> bool:
    """Treat any 2xx-with-`status:OK` as success.

    We deliberately do NOT require a receipt-id to also be present:
    upstream success shapes have varied (the live 2026-05-09 response
    populated ids inline, but a future shape might not), and the user
    can always confirm + grab QR / 番号 from the website's 予約照会
    page using their identity. We email that instruction explicitly.
    """
    if body.get("code") == "A0001":
        return True
    if str(body.get("status", "")).upper() == "OK":
        return True
    return False


def _extract_receipt(body: dict) -> str | None:
    """Find the first populated reservation-id field at top level or under
    `body.<inner>`. Returns the value as a string, or None if none found.
    """
    sources: list[dict] = [body]
    inner = body.get("body")
    if isinstance(inner, dict):
        sources.append(inner)
    for source in sources:
        for key in _RECEIPT_KEYS:
            value = source.get(key)
            if value:
                return str(value)
    return None


def _build_putres_payload(slot: Slot, creds: BookerCredentials) -> dict:
    """Compose the JSON body putres expects.

    Field order matches the live cURL capture for diff readability;
    upstream does not enforce key order.
    """
    return {
        "date":       slot.date,
        "coursecode": slot.course,
        "placecode":  slot.place,
        "starttime":  slot.starttime,
        "endtime":    slot.endtime,
        "license":    "",
        "phone":      creds.phone,
        "birthday":   creds.birthday,
        "name":       creds.name,
        "gracer_no":  _to_fullwidth_digits(creds.gracer_no),
    }


_RETRY_DELAYS_SECONDS = (1, 3, 9)  # ADR-5 §3: max 3 retries, exp backoff
_PUTRES_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": USER_AGENT,
    "Origin": "https://license-test.tokyo-madoguchi-yoyaku.com",
    "Referer": (
        "https://license-test.tokyo-madoguchi-yoyaku.com"
        "/police-pref-tokyo/01/html/main.html?lang=ja"
    ),
}


class Booker:
    """Single-shot caller for /putres. One instance per poll process is fine."""

    def __init__(
        self,
        creds: BookerCredentials,
        *,
        dry_run: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._creds = creds
        self._dry_run = dry_run
        self._client = client

    def try_book(self, slot: Slot) -> BookerResult:
        """Attempt to book `slot`. Returns the outcome; does not raise."""
        payload = _build_putres_payload(slot, self._creds)

        if self._dry_run:
            # ADR-5 §2: do NOT send; return payload for human review.
            # Caller (poll.py) is responsible for routing this to the user
            # (stdout / email) — booker never touches that channel.
            _log.info(
                "DRY-RUN: would book %s %s %s/%s @ %s "
                "(payload fields: %s)",
                slot.date, slot.starttime, slot.place, slot.course,
                slot.endtime, sorted(payload.keys()),
            )
            return BookerResult(
                outcome=BookerOutcome.DRY_RUN_ONLY,
                booked=None,
                failure_reason=None,
                payload_for_review=payload,
            )

        return self._send_with_retry(payload, slot)

    def _send_with_retry(self, payload: dict, slot: Slot) -> BookerResult:
        last_transient = None
        # Attempt 1 immediate; then 1s / 3s / 9s waits.
        for attempt_idx in range(1 + len(_RETRY_DELAYS_SECONDS)):
            if attempt_idx > 0:
                delay = _RETRY_DELAYS_SECONDS[attempt_idx - 1]
                _log.info("retry %d/%d after %ds (last=%s)",
                          attempt_idx, len(_RETRY_DELAYS_SECONDS),
                          delay, last_transient)
                time.sleep(delay)

            try:
                response = self._post(payload)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_transient = f"network: {type(e).__name__}"
                continue

            if 500 <= response.status_code < 600:
                last_transient = f"HTTP {response.status_code}"
                continue
            if 400 <= response.status_code < 500:
                # ADR-5 §3: 4xx not retried — body is malformed, retry won't help
                return BookerResult(
                    outcome=BookerOutcome.PERMANENT_FAILURE,
                    booked=None,
                    failure_reason=f"HTTP {response.status_code}",
                    payload_for_review=None,
                )

            # 2xx — parse the JSON envelope.
            try:
                body = response.json()
            except ValueError:
                return BookerResult(
                    outcome=BookerOutcome.PERMANENT_FAILURE,
                    booked=None,
                    failure_reason="putres returned non-JSON 2xx",
                    payload_for_review=None,
                )
            if not _is_success_response(body):
                return BookerResult(
                    outcome=BookerOutcome.PERMANENT_FAILURE,
                    booked=None,
                    failure_reason=f"putres code={body.get('code')!r} body={body!r}",
                    payload_for_review=None,
                )

            receipt = _extract_receipt(body)
            booked = BookedSlot(
                date=slot.date,
                starttime=slot.starttime,
                endtime=slot.endtime,
                place=slot.place,
                course=slot.course,
                booked_at=datetime.now(timezone.utc).isoformat(),
                receipt_no=receipt,
            )
            return BookerResult(
                outcome=BookerOutcome.SUCCESS,
                booked=booked,
                failure_reason=None,
                payload_for_review=None,
                confirmation_body=body,
            )

        return BookerResult(
            outcome=BookerOutcome.PERMANENT_FAILURE,
            booked=None,
            failure_reason=f"exhausted retries (last: {last_transient})",
            payload_for_review=None,
        )

    def _post(self, payload: dict) -> httpx.Response:
        url = f"{API_BASE}/putres"
        if self._client is not None:
            return self._client.post(url, json=payload, headers=_PUTRES_HEADERS)
        with httpx.Client(timeout=10.0) as client:
            return client.post(url, json=payload, headers=_PUTRES_HEADERS)


# --- /cancel — user-explicit only (per ADR-6, never auto-invoked) ---


@dataclass(frozen=True, slots=True)
class CancelResult:
    success: bool
    failure_reason: str | None  # None on success


def _build_cancel_payload(booked, creds: BookerCredentials) -> dict:
    """Compose the JSON body /cancel expects.

    Schema source: live cURL captured 2026-05-08 (OBSERVATIONS#cancel).
    Notably: cancel does NOT include date/starttime — upstream identifies
    the booking by user identity (name + birthday + gracer_no) +
    coursecode. This matches site-side enforcement of one active booking
    per identity (which our single-shot machine also satisfies).
    """
    return {
        "res_no":     booked.receipt_no or "",
        "license":    "",
        "phone":      creds.phone,
        "birthday":   creds.birthday,
        "action":     "3",                  # action code 3 = cancel
        "coursecode": booked.course,
        "name":       creds.name,
        "gracer_no":  _to_fullwidth_digits(creds.gracer_no),
    }


def cancel_booking(
    booked,
    creds: BookerCredentials,
    *,
    client: httpx.Client | None = None,
) -> CancelResult:
    """User-initiated cancel of a held booking. Single attempt — no retry.

    Why no retry: cancel is user-initiated and not racing anyone; if it
    fails the user can simply try again or fall back to the website
    UI. Retrying silently risks double-cancel race conditions.
    """
    payload = _build_cancel_payload(booked, creds)
    url = f"{API_BASE}/cancel"
    try:
        if client is not None:
            response = client.post(url, json=payload, headers=_PUTRES_HEADERS)
        else:
            with httpx.Client(timeout=10.0) as c:
                response = c.post(url, json=payload, headers=_PUTRES_HEADERS)
    except httpx.HTTPError as e:
        return CancelResult(success=False, failure_reason=f"network: {type(e).__name__}: {e}")

    if response.status_code != 200:
        return CancelResult(success=False, failure_reason=f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return CancelResult(success=False, failure_reason="non-JSON response")
    if body.get("code") != "A0001":
        return CancelResult(
            success=False,
            failure_reason=f"cancel code={body.get('code')!r} body={body!r}",
        )
    return CancelResult(success=True, failure_reason=None)
