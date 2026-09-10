"""Per-user reservation request — what we're trying to grab a slot for.

v0 keeps this in code / a small JSON file rather than a config-file
parser; v2's web layer will replace it with a per-user DB row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    candidate_places: tuple[str, ...]
    candidate_courses: tuple[str, ...]
    latest_acceptable_date: date  # inclusive
    earliest_acceptable_date: date = date.min  # inclusive; default = no lower bound

    def matches(self, place: str, course: str, slot_date: date) -> bool:
        return (
            place in self.candidate_places
            and course in self.candidate_courses
            and self.earliest_acceptable_date <= slot_date <= self.latest_acceptable_date
        )


def load_from_file(path: Path) -> ReservationRequest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ReservationRequest(
        candidate_places=tuple(raw["candidate_places"]),
        candidate_courses=tuple(raw["candidate_courses"]),
        latest_acceptable_date=date.fromisoformat(raw["latest_acceptable_date"]),
        earliest_acceptable_date=(
            date.fromisoformat(raw["earliest_acceptable_date"])
            if raw.get("earliest_acceptable_date") else date.min
        ),
    )
