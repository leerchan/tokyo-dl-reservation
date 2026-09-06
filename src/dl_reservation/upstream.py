"""Read-only client for the calgetres month endpoint.

The site exposes a JSON-over-HTTP API with no auth / CSRF / captcha.
We only call the read-only endpoint here; writes (putres / cancel) are
intentionally not exposed in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import httpx

from .codes import API_BASE, USER_AGENT, USER_TOKEN


class CalGetResError(RuntimeError):
    """Upstream returned a non-success code or unexpected payload shape."""


@dataclass(frozen=True, slots=True, order=True)
class Slot:
    """A single bookable slot, as returned by /calgetres.

    Ordering matches (date, starttime, place, course) so sorted snapshots
    diff cleanly.
    """

    date: str       # YYYYMMDD
    starttime: str  # HHMM
    endtime: str    # HHMM
    place: str      # placecode, e.g. "270"
    course: str     # coursecode, e.g. "11"
    capacity: int
    reservation: int
    displaytime: str  # ja label, kept for human-readable notifications

    @property
    def remaining(self) -> int:
        return max(self.capacity - self.reservation, 0)

    @property
    def is_open(self) -> bool:
        return self.capacity > self.reservation

    @property
    def date_obj(self) -> date:
        return date(int(self.date[:4]), int(self.date[4:6]), int(self.date[6:8]))

    @property
    def start_datetime(self) -> datetime:
        """Local-naive start datetime — slot times are JST, host runs JST."""
        return datetime(
            int(self.date[:4]), int(self.date[4:6]), int(self.date[6:8]),
            int(self.starttime[:2]), int(self.starttime[2:]),
        )


def fetch_month(
    place: str,
    course: str,
    yyyymm: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> list[Slot]:
    """Fetch every slot the upstream knows about for one (place, course, month).

    Returns the slots verbatim from `body[]`, with no client-side filtering —
    callers decide whether to keep past-dated entries (snapshot diffing
    needs the full set; user-facing notifications filter to future slots).
    """
    params = {
        "date": yyyymm,
        "coursecode": course,
        "placecode": place,
        "user": USER_TOKEN,
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Origin": "https://license-test.tokyo-madoguchi-yoyaku.com",
        "Referer": (
            "https://license-test.tokyo-madoguchi-yoyaku.com"
            "/police-pref-tokyo/01/html/main.html?lang=ja"
        ),
    })
    try:
        resp = client.get(f"{API_BASE}/calgetres", params=params)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owns_client:
            client.close()

    code = payload.get("code")
    if code != "A0001":
        raise CalGetResError(
            f"calgetres returned code={code!r} for "
            f"place={place} course={course} month={yyyymm}"
        )

    body = payload.get("body") or []
    return [
        Slot(
            date=item["date"],
            starttime=item["starttime"],
            endtime=item["endtime"],
            place=place,
            course=course,
            capacity=int(item["capacity"]),
            reservation=int(item["reservation"]),
            displaytime=item.get("displaytime", ""),
        )
        for item in body
    ]
