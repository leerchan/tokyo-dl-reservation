import base64
from datetime import date
from unittest.mock import patch

import pytest

from dl_reservation.booking_state import BookedSlot
from dl_reservation.notifier import (
    BarkNotifier,
    EmailNotifier,
    HeartbeatPayload,
    StdoutNotifier,
    TeeNotifier,
    _extract_attachments,
    _extract_id_fields,
)
from dl_reservation.upstream import Slot

# 1×1 transparent PNG for tests that need real PNG magic bytes.
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
    b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
    b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PNG_1X1_B64 = base64.b64encode(_PNG_1X1).decode("ascii")


def _heartbeat() -> HeartbeatPayload:
    return HeartbeatPayload(
        deadline=date(2026, 5, 20),
        place_count=3,
        course_count=1,
        total_slots_in_window=54,
        last_poll_at="2026-05-08T18:50:00+00:00",
    )


def _slot() -> Slot:
    return Slot(
        date="20260601", starttime="0800", endtime="0930",
        place="270", course="11",
        capacity=100, reservation=98,
        displaytime="x",
    )


def test_email_notifier_from_env_requires_credentials(monkeypatch):
    for v in ("DL_RES_SMTP_USER", "DL_RES_SMTP_PASSWORD", "DL_RES_EMAIL_TO"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RuntimeError, match="missing required env vars"):
        EmailNotifier.from_env()


def test_email_notifier_skips_send_when_empty(monkeypatch):
    monkeypatch.setenv("DL_RES_SMTP_USER", "u@example.com")
    monkeypatch.setenv("DL_RES_SMTP_PASSWORD", "x")
    monkeypatch.setenv("DL_RES_EMAIL_TO", "u@example.com")
    sent = []
    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value.send_message = (
            lambda m: sent.append(m)
        )
        EmailNotifier().notify([])
    assert sent == []
    mock_smtp.assert_not_called()


def test_email_notifier_sends_when_openings(monkeypatch):
    monkeypatch.setenv("DL_RES_SMTP_USER", "u@example.com")
    monkeypatch.setenv("DL_RES_SMTP_PASSWORD", "x")
    monkeypatch.setenv("DL_RES_EMAIL_TO", "u@example.com,b@example.com")
    sent = []
    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value.send_message = (
            lambda m: sent.append(m)
        )
        EmailNotifier().notify([_slot()])
    assert len(sent) == 1
    subject = sent[0]["Subject"]
    assert subject.startswith("[dl-reservation] 空席 ×1:")
    assert "06/01" in subject
    assert "u@example.com, b@example.com" == sent[0]["To"]
    body = sent[0].get_content()
    assert "2026-06-01" in body                 # ISO date
    assert "08:00–09:30" in body                # human-friendly time range
    assert "免許種別" in body                   # license-type label
    assert "予約手順" in body                   # booking instructions
    assert "license-renew.tokyo-madoguchi-yoyaku.com" in body  # entry page, not deep-link
    assert "学科試験" in body                   # which radio to pick


def test_tee_notifier_isolates_failures():
    calls = []

    class Boom:
        def notify(self, slots):
            raise RuntimeError("planned")
        def heartbeat(self, payload):
            raise RuntimeError("planned")

    class Capture:
        def notify(self, slots):
            calls.append(("notify", list(slots)))
        def heartbeat(self, payload):
            calls.append(("heartbeat", payload))

    TeeNotifier(Boom(), Capture()).notify([_slot()])
    TeeNotifier(Boom(), Capture()).heartbeat(_heartbeat())
    assert len(calls) == 2
    assert calls[0][0] == "notify"
    assert calls[1][0] == "heartbeat"


def test_stdout_notifier_emits_per_slot(caplog):
    caplog.set_level("INFO", logger="dl_reservation.notify")
    StdoutNotifier().notify([_slot()])
    assert any("OPEN 20260601" in r.message for r in caplog.records)


def test_stdout_notifier_emits_silent_message_when_empty(caplog):
    caplog.set_level("INFO", logger="dl_reservation.notify")
    StdoutNotifier().notify([])
    assert any("no new openings" in r.message for r in caplog.records)


def test_stdout_notifier_heartbeat_logs_summary(caplog):
    caplog.set_level("INFO", logger="dl_reservation.notify")
    StdoutNotifier().heartbeat(_heartbeat())
    assert any("HEARTBEAT" in r.message and "2026-05-20" in r.message
               for r in caplog.records)


def _booked_slot() -> BookedSlot:
    return BookedSlot(
        date="20260515", starttime="0800", endtime="0930",
        place="270", course="11",
        booked_at="2026-05-09T01:00:00+00:00",
        receipt_no="R12345",
    )


def test_extract_attachments_decodes_qr_field_as_png():
    body = {"code": "A0001", "body": {"receipt_no": "R12345", "qr": _PNG_1X1_B64}}
    atts = _extract_attachments(body)
    qr_atts = [a for a in atts if a.filename.startswith("qr.")]
    assert len(qr_atts) == 1
    assert qr_atts[0].subtype == "png"
    assert qr_atts[0].data == _PNG_1X1
    # confirmation.json fallback always present
    assert any(a.filename == "confirmation.json" for a in atts)


def test_extract_attachments_finds_image_under_unrelated_key():
    # Upstream might use a key we don't anticipate — magic-byte sniff catches it.
    body = {"code": "A0001", "body": {"some_blob": _PNG_1X1_B64}}
    atts = _extract_attachments(body)
    image_atts = [a for a in atts if a.filename.startswith("image-")]
    assert len(image_atts) == 1
    assert image_atts[0].subtype == "png"


def test_extract_attachments_keeps_qr_text_when_not_image():
    body = {"body": {"qr_code": "ABC-NOT-BASE64-IMAGE"}}
    atts = _extract_attachments(body)
    txt_atts = [a for a in atts if a.filename.endswith(".txt")]
    assert len(txt_atts) == 1
    assert txt_atts[0].data == b"ABC-NOT-BASE64-IMAGE"


def test_extract_attachments_handles_none_body():
    assert _extract_attachments(None) == []
    assert _extract_attachments({}) == []


def test_extract_id_fields_surfaces_reservation_and_receipt_numbers():
    body = {
        "code": "A0001",  # noise — excluded
        "currenttime": "1778231720",  # noise — excluded
        "body": {
            "receipt_no": "R12345",
            "reservation_no": "Y99887",
            "yoyaku_no": "ＹＹＹ123",
            "uketsuke_no": "U-456",
            "qr": _PNG_1X1_B64,  # QR — excluded from ID surface
            "displaytime": "午前試験",  # not ID-like
        },
    }
    found = dict(_extract_id_fields(body))
    assert "body.receipt_no" in found
    assert "body.reservation_no" in found
    assert "body.yoyaku_no" in found
    assert "body.uketsuke_no" in found
    assert "code" not in found
    assert "currenttime" not in found
    assert not any(p.endswith(".qr") for p in found)


def test_email_notifier_booked_includes_qr_attachment_and_id_in_body(monkeypatch):
    monkeypatch.setenv("DL_RES_SMTP_USER", "u@example.com")
    monkeypatch.setenv("DL_RES_SMTP_PASSWORD", "x")
    monkeypatch.setenv("DL_RES_EMAIL_TO", "u@example.com")
    sent = []
    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value.send_message = (
            lambda m: sent.append(m)
        )
        EmailNotifier().booked(
            _booked_slot(),
            confirmation_body={
                "code": "A0001",
                "body": {
                    "receipt_no": "R12345",
                    "yoyaku_no": "Y99887",
                    "qr": _PNG_1X1_B64,
                },
            },
        )
    assert len(sent) == 1
    msg = sent[0]
    assert "🎉 予約成立" in msg["Subject"]
    parts = list(msg.iter_attachments())
    filenames = [p.get_filename() for p in parts]
    assert "qr.png" in filenames
    assert "confirmation.json" in filenames
    qr_part = next(p for p in parts if p.get_filename() == "qr.png")
    assert qr_part.get_payload(decode=True) == _PNG_1X1
    # ID surface in body — user can see reservation # without opening attachments.
    text_part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    body_text = text_part.get_content()
    assert "yoyaku_no" in body_text
    assert "Y99887" in body_text
    assert "R12345" in body_text  # receipt_no still in 受付番号 line


def test_email_notifier_booked_works_when_no_confirmation_body(monkeypatch):
    monkeypatch.setenv("DL_RES_SMTP_USER", "u@example.com")
    monkeypatch.setenv("DL_RES_SMTP_PASSWORD", "x")
    monkeypatch.setenv("DL_RES_EMAIL_TO", "u@example.com")
    sent = []
    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value.send_message = (
            lambda m: sent.append(m)
        )
        EmailNotifier().booked(_booked_slot())
    assert len(sent) == 1


def test_email_notifier_heartbeat_sends_with_status_subject(monkeypatch):
    monkeypatch.setenv("DL_RES_SMTP_USER", "u@example.com")
    monkeypatch.setenv("DL_RES_SMTP_PASSWORD", "x")
    monkeypatch.setenv("DL_RES_EMAIL_TO", "u@example.com")
    sent = []
    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value.send_message = (
            lambda m: sent.append(m)
        )
        EmailNotifier().heartbeat(_heartbeat())
    assert len(sent) == 1
    assert "still no slot ≤ 2026-05-20" in sent[0]["Subject"]
    body = sent[0].get_content()
    assert "2026-05-20" in body
    assert "3 place(s)" in body


def test_bark_notifier_posts_openings_and_skips_empty(monkeypatch):
    monkeypatch.setenv("DL_RES_BARK_URL", "https://api.day.app/devicekey/")
    with patch("dl_reservation.notifier.httpx.post") as post:
        n = BarkNotifier.from_env()
        n.notify([])
        assert post.call_count == 0
        n.notify([_slot()])
        post.assert_called_once()
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        assert url == "https://api.day.app/devicekey"
        assert payload["title"] == "空席 ×1"
        assert "府中" in payload["body"] and "0800-0930" in payload["body"]
        assert payload["level"] == "timeSensitive"


def test_bark_notifier_from_env_requires_url(monkeypatch):
    monkeypatch.delenv("DL_RES_BARK_URL", raising=False)
    with pytest.raises(RuntimeError, match="DL_RES_BARK_URL"):
        BarkNotifier.from_env()
