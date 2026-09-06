"""Notification fan-out.

v0 ships two notifier implementations:
- `StdoutNotifier`  — always-on log output, useful for cron tail.
- `EmailNotifier`   — Gmail-friendly SMTP_SSL (port 465). SMTP credentials
                      are loaded from environment variables, never config
                      files, so the project itself stays secret-free.

Compose multiple notifiers with `TeeNotifier` to send the same alert
through more than one channel at once.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import smtplib
from dataclasses import dataclass

import httpx
from datetime import date
from email.message import EmailMessage
from typing import Iterable, Protocol

from .booking_state import BookedSlot
from .codes import COURSES, PLACES
from .upstream import Slot


@dataclass(frozen=True, slots=True)
class HeartbeatPayload:
    """Inputs for a 'still no qualifying slot' digest email.

    Sent at most once per `heartbeat.HEARTBEAT_INTERVAL` so the user can
    tell a dead cron from a quiet day.
    """

    deadline: date
    place_count: int
    course_count: int
    total_slots_in_window: int
    last_poll_at: str  # ISO-8601 UTC


class Notifier(Protocol):
    def notify(self, openings: Iterable[Slot]) -> None: ...

    def heartbeat(self, payload: HeartbeatPayload) -> None: ...

    def booked(
        self, slot: BookedSlot, *, confirmation_body: dict | None = None
    ) -> None: ...

    def booking_failed(self, target: Slot, reason: str) -> None: ...

    def dry_run_payload(self, target: Slot, payload: dict) -> None: ...


_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _format_slot_line(slot: Slot) -> str:
    """Compact one-liner for stdout / log."""
    return (
        f"{slot.date} {PLACES.get(slot.place, slot.place)} "
        f"{slot.starttime}-{slot.endtime} "
        f"[{COURSES.get(slot.course, slot.course)}/{slot.course}] "
        f"cap={slot.capacity} res={slot.reservation} ({slot.displaytime})"
    )


def _format_slot_email_block(slot: Slot) -> str:
    """Multi-line, action-oriented block for the email body.

    Tells the user concretely what to pick on the booking page:
    date+weekday, time, center, license category to select, remaining
    seats, and the displaytime label that the upstream UI uses.
    """
    d = slot.date_obj
    wd = _WEEKDAY_JA[d.weekday()]
    iso_date = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    time_range = (
        f"{slot.starttime[:2]}:{slot.starttime[2:]}–"
        f"{slot.endtime[:2]}:{slot.endtime[2:]}"
    )
    place_name = PLACES.get(slot.place, slot.place)
    course_name = COURSES.get(slot.course, slot.course)
    return (
        f"  日付       : {iso_date} ({wd})\n"
        f"  時間帯     : {time_range}   ({slot.displaytime})\n"
        f"  試験場     : {place_name}   (placecode={slot.place})\n"
        f"  免許種別   : {course_name}   (coursecode={slot.course})\n"
        f"  空席       : {slot.remaining} / {slot.capacity} "
        f"(已予約 {slot.reservation})\n"
    )


# index_000.html on the renew domain is the umbrella entry page for all
# three reservation flows (免許更新 / 仮免許学科試験 / 学科試験). Deep-
# linking straight to license-test/main.html skips the radio-button
# router and leaves the SPA without initialized state, so the page
# appears "broken" — verified by user 2026-05-08 evening.
_BOOKING_URL = (
    "https://license-renew.tokyo-madoguchi-yoyaku.com"
    "/police-pref-tokyo/index_000.html"
)


# --- Confirmation-body QR / image extraction ---
#
# The upstream /putres response shape isn't fully captured (we have only the
# happy-path receipt_no path mapped). When the user goes live we don't yet
# know which key carries the QR — the spec mentions a separate /getcrypto
# endpoint, but on a successful putres the response itself may also embed
# image data. To stay robust against unknown keys, we (1) walk the entire
# body, (2) heuristically classify any string field that looks like an
# image / QR payload, and (3) ALWAYS attach the raw JSON so the user can
# manually recover even when heuristics miss.

_QR_KEY_HINTS = ("qr", "qrcode", "qr_code", "qrImage", "qr_image",
                 "image", "code_image", "crypto", "barcode")

# Key-name fragments that suggest "this string is an ID / 予約番号 / 受付番号
# / receipt / reservation number." Used to surface these directly in the
# email body so the user sees them without opening JSON attachments — the
# user explicitly asked: forgot to screenshot the previous booking and
# does not want to re-book just to find the number.
_ID_KEY_HINTS = (
    "receipt", "reservation", "yoyaku", "uketsuke", "ticket",
    "予約番号", "受付番号", "予約", "受付",
    "_no", "no_", "number", "slip", "confirmation",
)
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image", "png", "png"),
    (b"\xff\xd8\xff",      "image", "jpeg", "jpg"),
    (b"GIF87a",            "image", "gif", "gif"),
    (b"GIF89a",            "image", "gif", "gif"),
    (b"<svg",              "image", "svg+xml", "svg"),
)


@dataclass(frozen=True, slots=True)
class _Attachment:
    filename: str
    maintype: str
    subtype: str
    data: bytes
    note: str            # human-readable provenance for the email body


def _walk_strings(node, path: tuple[str, ...] = ()):
    """Yield (path, value) for every string leaf in a nested dict/list."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, path + (f"[{i}]",))
    elif isinstance(node, str):
        yield path, node


def _qr_field_paths(body: dict | None) -> list[str]:
    """Return dotted paths whose key name hints at a QR / image field."""
    if not body:
        return []
    hits: list[str] = []
    for path, _ in _walk_strings(body):
        leaf = path[-1].lower() if path else ""
        if any(h in leaf for h in _QR_KEY_HINTS):
            hits.append(".".join(path))
    return hits


# Generic API envelope keys that match `_ID_KEY_HINTS` ("code") or look ID-y
# but are noise. Exclude them from the body surface so we don't drown the
# user in `code: A0001` / `currenttime: ...` lines.
_ID_NOISE_PATHS = frozenset({"code", "currenttime", "message"})


def _extract_id_fields(body: dict | None) -> list[tuple[str, str]]:
    """Return (dotted_path, value) string fields that look like reservation /
    receipt numbers. Skips QR-image fields and known envelope noise.
    """
    if not body:
        return []
    out: list[tuple[str, str]] = []
    for path, value in _walk_strings(body):
        if not path:
            continue
        dotted = ".".join(path)
        if dotted in _ID_NOISE_PATHS:
            continue
        leaf = path[-1]
        leaf_lower = leaf.lower()
        if any(h in leaf_lower for h in _QR_KEY_HINTS):
            continue  # QR strings go to attachments, not the body
        if not any(h in leaf_lower or h in leaf for h in _ID_KEY_HINTS):
            continue
        # Long base64-ish blobs are not human IDs — keep the body readable.
        if len(value) > 200:
            continue
        out.append((dotted, value))
    return out


def _classify_image_bytes(data: bytes) -> tuple[str, str, str] | None:
    """If `data` starts with a known image magic, return (maintype, subtype, ext)."""
    for magic, maintype, subtype, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return maintype, subtype, ext
    return None


def _try_decode_base64(value: str) -> bytes | None:
    """Best-effort base64 decode. Strips data-URL prefix if present."""
    s = value.strip()
    if s.startswith("data:"):
        comma = s.find(",")
        if comma == -1:
            return None
        s = s[comma + 1:]
    s = "".join(s.split())  # drop whitespace/newlines
    if len(s) < 16 or len(s) % 4 != 0:
        return None
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None


def _extract_attachments(body: dict | None) -> list[_Attachment]:
    """Find image-like fields in the response and return them as attachments,
    plus a raw-JSON dump so nothing is ever lost.

    Heuristics, in order:
      1. Any string whose key name matches _QR_KEY_HINTS — try base64-decode;
         if the bytes have an image magic, attach as `qr.<ext>`. Otherwise
         attach the raw string as a `.txt` so the user can paste it into a
         QR-reader manually.
      2. Any string anywhere in the body whose base64-decoded bytes carry an
         image magic — attach as `image-<dottedpath>.<ext>` (catches the QR
         even if the upstream field name is unrelated).
      3. Always attach the entire response as `confirmation.json`.
    """
    if not body:
        return []

    attachments: list[_Attachment] = []
    seen_paths: set[str] = set()
    image_idx = 0

    # Pass 1 + 2 in a single walk.
    for path, value in _walk_strings(body):
        dotted = ".".join(path)
        if dotted in seen_paths:
            continue
        leaf = path[-1].lower() if path else ""
        is_qr_hint = any(h in leaf for h in _QR_KEY_HINTS)
        decoded = _try_decode_base64(value)
        kind = _classify_image_bytes(decoded) if decoded else None

        if kind is not None:
            maintype, subtype, ext = kind
            assert decoded is not None
            filename = (
                f"qr.{ext}" if is_qr_hint
                else f"image-{image_idx}.{ext}"
            )
            if not is_qr_hint:
                image_idx += 1
            attachments.append(_Attachment(
                filename=filename, maintype=maintype, subtype=subtype,
                data=decoded,
                note=f"decoded image at body.{dotted}",
            ))
            seen_paths.add(dotted)
        elif is_qr_hint:
            # Key looks like a QR field but value isn't an image — keep the
            # raw text so the user can recover manually.
            attachments.append(_Attachment(
                filename=f"qr-{leaf}.txt",
                maintype="text", subtype="plain",
                data=value.encode("utf-8"),
                note=f"raw text at body.{dotted} (no image magic detected)",
            ))
            seen_paths.add(dotted)

    # Always attach the full body as JSON — loss-less audit trail.
    attachments.append(_Attachment(
        filename="confirmation.json",
        maintype="application", subtype="json",
        data=json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
        note="full upstream response (audit + manual recovery)",
    ))
    return attachments


class StdoutNotifier:
    """Print a one-line summary per opening; safe in cron / launchd output."""

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logging.getLogger("dl_reservation.notify")

    def notify(self, openings: Iterable[Slot]) -> None:
        any_emitted = False
        for slot in openings:
            any_emitted = True
            self._log.info("OPEN %s", _format_slot_line(slot))
        if not any_emitted:
            self._log.info("no new openings")

    def heartbeat(self, payload: HeartbeatPayload) -> None:
        self._log.info(
            "HEARTBEAT no bookable slot ≤ %s across %d place(s) × %d course(s) "
            "(%d slots in window, last poll %s)",
            payload.deadline.isoformat(),
            payload.place_count,
            payload.course_count,
            payload.total_slots_in_window,
            payload.last_poll_at,
        )

    def booked(
        self, slot: BookedSlot, *, confirmation_body: dict | None = None
    ) -> None:
        qr_keys = _qr_field_paths(confirmation_body) if confirmation_body else []
        self._log.info(
            "BOOKED %s %s-%s %s/%s receipt=%s qr_fields=%s",
            slot.date, slot.starttime, slot.endtime,
            slot.place, slot.course, slot.receipt_no,
            qr_keys or "(none)",
        )

    def booking_failed(self, target: Slot, reason: str) -> None:
        self._log.warning(
            "BOOKING FAILED %s %s %s/%s — %s",
            target.date, target.starttime, target.place, target.course, reason,
        )

    def dry_run_payload(self, target: Slot, payload: dict) -> None:
        self._log.info(
            "DRY-RUN payload for %s %s %s/%s: keys=%s",
            target.date, target.starttime, target.place, target.course,
            sorted(payload.keys()),
        )


class EmailNotifier:
    """Send a single digest email per poll-run when openings exist.

    Credentials and addresses are read from environment, NOT from any
    config file checked into the repo:

        DL_RES_SMTP_HOST     default "smtp.gmail.com"
        DL_RES_SMTP_PORT     default "465"
        DL_RES_SMTP_USER     full Gmail address used for AUTH
        DL_RES_SMTP_PASSWORD Gmail app-specific password
        DL_RES_EMAIL_FROM    From: header (default = DL_RES_SMTP_USER)
        DL_RES_EMAIL_TO      To: header (comma-separated allowed)

    Empty notifications are suppressed — sending an email saying "nothing
    to see" every 10 minutes would defeat the point.
    """

    ENV_HOST = "DL_RES_SMTP_HOST"
    ENV_PORT = "DL_RES_SMTP_PORT"
    ENV_USER = "DL_RES_SMTP_USER"
    ENV_PASSWORD = "DL_RES_SMTP_PASSWORD"
    ENV_FROM = "DL_RES_EMAIL_FROM"
    ENV_TO = "DL_RES_EMAIL_TO"

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logging.getLogger("dl_reservation.notify.email")

    @classmethod
    def from_env(cls) -> "EmailNotifier":
        missing = [
            name
            for name in (cls.ENV_USER, cls.ENV_PASSWORD, cls.ENV_TO)
            if not os.environ.get(name)
        ]
        if missing:
            raise RuntimeError(
                f"EmailNotifier missing required env vars: {', '.join(missing)}"
            )
        return cls()

    def notify(self, openings: Iterable[Slot]) -> None:
        slots = list(openings)
        if not slots:
            return  # don't email "no openings" — heartbeat handles that path
        body = (
            "新しい空席が出ました — 数分で他のユーザーに取られます。\n"
            "(slot 可能在几分钟内被别人抢走,邮件到达后请立刻操作)\n\n"
            + "\n".join(_format_slot_email_block(s) for s in slots)
            + "\n"
            f"予約手順 / How to book:\n"
            f"  1. 下のリンクを開く: {_BOOKING_URL}\n"
            f"  2. 入口页面で「学科試験」(M336) のラジオボタンを選ぶ\n"
            f"     ※「免許更新」「仮免許学科試験」ではない\n"
            f"  3. 試験場で上記の試験場を選ぶ\n"
            f"  4. 免許の種類で上記の免許種別を選ぶ\n"
            f"  5. 当該日付・時間帯のセルが空になっていることを確認\n"
            f"  6. 受験番号・氏名・生年月日を入力して予約確定\n\n"
            f"※ 表示が「空席なし」になっていた場合、すでに他者に取られた\n"
            f"   可能性があります(本通知は API の最新スナップショットです)。\n"
        )
        slot_summary = ", ".join(
            f"{s.date[4:6]}/{s.date[6:8]} "
            f"{s.starttime[:2]}:{s.starttime[2:]} "
            f"{PLACES.get(s.place, s.place)}({s.remaining})"
            for s in slots
        )
        self._send(
            subject=f"[dl-reservation] 空席 ×{len(slots)}: {slot_summary}",
            body=body,
        )
        self._log.info("emailed %d opening(s): %s", len(slots), slot_summary)

    def heartbeat(self, payload: HeartbeatPayload) -> None:
        body = (
            f"No bookable slot on or before {payload.deadline.isoformat()} "
            f"across {payload.place_count} place(s) × "
            f"{payload.course_count} course(s).\n"
            f"Slots in window: {payload.total_slots_in_window} (all full).\n"
            f"Last poll: {payload.last_poll_at}.\n\n"
            "This email confirms the watcher is alive. You will receive a "
            "new one in 24h if the situation has not changed.\n"
        )
        self._send(
            subject=(
                f"[dl-reservation] still no slot ≤ {payload.deadline.isoformat()}"
            ),
            body=body,
        )
        self._log.info("emailed heartbeat (deadline=%s)", payload.deadline)

    def booked(
        self, slot: BookedSlot, *, confirmation_body: dict | None = None
    ) -> None:
        place_name = PLACES.get(slot.place, slot.place)
        course_name = COURSES.get(slot.course, slot.course)
        d = (
            f"{slot.date[:4]}-{slot.date[4:6]}-{slot.date[6:8]} "
            f"{slot.starttime[:2]}:{slot.starttime[2:]}"
        )

        attachments = _extract_attachments(confirmation_body)
        attachment_summary = (
            "\n".join(f"  • {a.filename} ({a.note})" for a in attachments)
            if attachments
            else "  (none — upstream returned no recognizable image/QR field)"
        )
        id_fields = _extract_id_fields(confirmation_body)
        id_summary = (
            "\n".join(f"  {p:24s}: {v}" for p, v in id_fields)
            if id_fields
            else "  (none — booker.receipt_no above is the only ID found)"
        )

        body = (
            "🎉 予約成立しました — booker has secured a slot for you.\n\n"
            f"  日付/時間   : {d}\n"
            f"  試験場       : {place_name} (placecode={slot.place})\n"
            f"  免許種別     : {course_name} (coursecode={slot.course})\n"
            f"  受付番号     : {slot.receipt_no or '(not returned in response — see step 1)'}\n"
            f"  予約成立時刻 : {slot.booked_at}\n\n"
            "✅ 必ずやってほしいこと(STEP 1 — 人工確認):\n"
            f"  1. 下記サイトを開いて、ご自身の予約情報(氏名・生年月日・"
            f"仮免許番号など)で 予約照会 → 本メールの日時 / 試験場 / "
            f"免許種別 が表示されることを確認:\n"
            f"     {_BOOKING_URL}\n"
            "  2. 予約番号 / 受付番号 を控える、QR コードを\n"
            "     スクリーンショット保存しておく(当日試験場で必要)。\n"
            "  → 上流 API の response に ID/QR が含まれない場合があるため、\n"
            "    自動抽出よりサイト側 予約照会 のほうが信頼できます。\n"
            "    多一步人工核验,体验上更放心。\n\n"
            "📋 自動抽出した参考情報(上流 response 内の ID 系フィールド):\n"
            f"{id_summary}\n\n"
            "添付ファイル / Attachments:\n"
            f"{attachment_summary}\n\n"
            "次のステップ(当日まで):\n"
            "  • 予約日に試験場へ持っていくべき書類を再確認\n"
            "  • 当日の集合時間(受付時間)を再確認\n"
            f"  • 不要になった場合: 自分で {_BOOKING_URL} の「予約キャンセル」\n"
            "    フローから取消し、かつ `dl-poll --reset-booking` で booker\n"
            "    を再アクティベート(本ツールは自動取消はしません)\n"
        )
        msg = self._compose(
            subject=(
                f"[dl-reservation] 🎉 予約成立 {slot.date[4:6]}/{slot.date[6:8]} "
                f"{slot.starttime[:2]}:{slot.starttime[2:]} {place_name}"
            ),
            body=body,
        )
        for att in attachments:
            msg.add_attachment(
                att.data,
                maintype=att.maintype,
                subtype=att.subtype,
                filename=att.filename,
            )
        self._dispatch(msg)
        self._log.info(
            "emailed booking confirmation (date=%s receipt=%s attachments=%d)",
            slot.date, slot.receipt_no, len(attachments),
        )

    def booking_failed(self, target: Slot, reason: str) -> None:
        body = (
            "Booker attempted to grab the slot below but failed.\n"
            "v1 will NOT auto-retry; the slot may have been taken or the\n"
            "credentials/payload may be wrong.\n\n"
            f"  対象 slot   : {target.date} {target.starttime} "
            f"{PLACES.get(target.place, target.place)}/{target.course}\n"
            f"  失敗理由   : {reason}\n\n"
            "⚠️  POSSIBLE FALSE NEGATIVE — putres is NOT idempotent on the wire:\n"
            "    if attempt 1 timed out *after* the server committed your booking,\n"
            "    a retry would see 'you already have a booking' and we'd report\n"
            "    failure even though the slot is yours. **Please log in to the\n"
            f"    booking site ({_BOOKING_URL}) and check 予約照会 before\n"
            "    assuming this slot was lost.** If the booking is in fact held,\n"
            "    run `dl-poll --reset-booking` is NOT what you want — instead\n"
            "    create state/booked.json manually so the booker stops trying.\n\n"
            "If this is a credential / payload issue: re-check `.env.local`\n"
            "and the DL_RES_BOOKER_* values.\n"
            "If the slot was actually taken: nothing to do — wait for next opening.\n"
        )
        self._send(
            subject=(
                f"[dl-reservation] ⚠️ booking FAILED "
                f"{target.date[4:6]}/{target.date[6:8]} {target.starttime}"
            ),
            body=body,
        )
        self._log.info("emailed booking failure: %s", reason)

    def dry_run_payload(self, target: Slot, payload: dict) -> None:
        # Per ADR-5 §2: human-readable payload review before unlocking real send.
        # NOTE: payload contains user identity fields (name, birthday, phone,
        # gracer_no). Email goes to the user's own DL_RES_EMAIL_TO inbox, so
        # this is acceptable; we still avoid logging values to stdout.
        place_name = PLACES.get(target.place, target.place)
        body = (
            "🧪 DRY-RUN: booker would have submitted the JSON below. NO actual\n"
            "putres call was sent to the upstream server.\n\n"
            f"  対象 slot   : {target.date} {target.starttime}-{target.endtime}\n"
            f"                 {place_name} (placecode={target.place})\n"
            f"                 {COURSES.get(target.course, target.course)} (coursecode={target.course})\n\n"
            "Payload that would have been POST'd to /putres:\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "Review checklist (per ADR-5 §2):\n"
            "  □ name / gracer_no fullwidth correctly?\n"
            "  □ birthday / phone half-width correct?\n"
            "  □ slot date / time / place / course as expected?\n\n"
            "If all correct: re-launch with `dl-poll ... --enable-booker --book-real`\n"
            "to unlock real submission. The next matching opening will be\n"
            "actually booked.\n"
        )
        self._send(
            subject=(
                f"[dl-reservation] 🧪 DRY-RUN payload review "
                f"({target.date[4:6]}/{target.date[6:8]} {target.starttime})"
            ),
            body=body,
        )
        self._log.info("emailed dry-run payload for review")

    def _send(self, subject: str, body: str) -> None:
        self._dispatch(self._compose(subject, body))

    def _compose(self, subject: str, body: str) -> EmailMessage:
        sender = os.environ.get(self.ENV_FROM, os.environ[self.ENV_USER])
        recipients = [
            addr.strip()
            for addr in os.environ[self.ENV_TO].split(",")
            if addr.strip()
        ]
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)
        return msg

    def _dispatch(self, msg: EmailMessage) -> None:
        host = os.environ.get(self.ENV_HOST, "smtp.gmail.com")
        port = int(os.environ.get(self.ENV_PORT, "465"))
        user = os.environ[self.ENV_USER]
        password = os.environ[self.ENV_PASSWORD]
        with smtplib.SMTP_SSL(host, port) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)


class BarkNotifier:
    """iOS push via Bark (https://bark.day.app).

    One env var: the push URL, `https://api.day.app/<device_key>` or a
    self-hosted equivalent. Bark accepts a JSON POST to that URL.
    """

    ENV_URL = "DL_RES_BARK_URL"
    _MAX_LINES = 8  # ponytail: push body is small; email has the full list

    def __init__(self, url: str, log: logging.Logger | None = None) -> None:
        self._url = url.rstrip("/")
        self._log = log or logging.getLogger("dl_reservation.notify.bark")

    @classmethod
    def from_env(cls) -> "BarkNotifier":
        url = os.environ.get(cls.ENV_URL)
        if not url:
            raise RuntimeError(f"missing required env vars: {cls.ENV_URL}")
        return cls(url)

    def notify(self, openings: Iterable[Slot]) -> None:
        slots = list(openings)
        if not slots:
            return
        lines = [_format_slot_line(s) for s in slots]
        more = f"\n…+{len(lines) - self._MAX_LINES}" if len(lines) > self._MAX_LINES else ""
        self._push(f"空席 ×{len(slots)}", "\n".join(lines[: self._MAX_LINES]) + more)

    def heartbeat(self, payload: HeartbeatPayload) -> None:
        self._push(
            "heartbeat: まだ空席なし",
            f"≤{payload.deadline.isoformat()} / "
            f"{payload.total_slots_in_window} slots in window / "
            f"last poll {payload.last_poll_at}",
            level="passive",
        )

    def booked(
        self, slot: BookedSlot, *, confirmation_body: dict | None = None
    ) -> None:
        body = (
            f"{slot.date} {slot.starttime}-{slot.endtime} "
            f"{PLACES.get(slot.place, slot.place)} receipt={slot.receipt_no}"
        )
        body += "".join(f"\n{k}={v}" for k, v in _extract_id_fields(confirmation_body))
        self._push("予約成功 — サイトで要確認", body, level="critical")

    def booking_failed(self, target: Slot, reason: str) -> None:
        self._push("予約失敗", f"{_format_slot_line(target)}\n{reason}")

    def dry_run_payload(self, target: Slot, payload: dict) -> None:
        self._push(
            "dry-run payload",
            f"{_format_slot_line(target)}\nkeys={sorted(payload)}",
            level="passive",
        )

    def _push(self, title: str, body: str, *, level: str = "timeSensitive") -> None:
        resp = httpx.post(
            self._url,
            json={"title": title, "body": body, "group": "dl-reservation", "level": level},
            timeout=10.0,
        )
        resp.raise_for_status()
        self._log.info("bark pushed: %s", title)


class TeeNotifier:
    """Fan out to multiple notifiers; one failure does not block the others."""

    def __init__(self, *children: Notifier, log: logging.Logger | None = None) -> None:
        self._children = children
        self._log = log or logging.getLogger("dl_reservation.notify.tee")

    def notify(self, openings: Iterable[Slot]) -> None:
        slots = list(openings)
        for child in self._children:
            try:
                child.notify(slots)
            except Exception:
                self._log.exception(
                    "notifier %s failed; continuing with others",
                    type(child).__name__,
                )

    def heartbeat(self, payload: HeartbeatPayload) -> None:
        for child in self._children:
            try:
                child.heartbeat(payload)
            except Exception:
                self._log.exception(
                    "notifier %s heartbeat failed; continuing with others",
                    type(child).__name__,
                )

    def booked(
        self, slot: BookedSlot, *, confirmation_body: dict | None = None
    ) -> None:
        for child in self._children:
            try:
                child.booked(slot, confirmation_body=confirmation_body)
            except Exception:
                self._log.exception(
                    "notifier %s booked failed; continuing with others",
                    type(child).__name__,
                )

    def booking_failed(self, target: Slot, reason: str) -> None:
        for child in self._children:
            try:
                child.booking_failed(target, reason)
            except Exception:
                self._log.exception(
                    "notifier %s booking_failed failed; continuing with others",
                    type(child).__name__,
                )

    def dry_run_payload(self, target: Slot, payload: dict) -> None:
        for child in self._children:
            try:
                child.dry_run_payload(target, payload)
            except Exception:
                self._log.exception(
                    "notifier %s dry_run_payload failed; continuing with others",
                    type(child).__name__,
                )
