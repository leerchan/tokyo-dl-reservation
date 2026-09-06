# tokyo-dl-reservation

> 警视庁・運転免許試験予約システム(東京 3 試験場 / 学科試験)の空席監視 & 自動予約ツール
> A polling + auto-booking tool for Tokyo's driver-license written exam reservation system.

---

## ⚠️ Use it responsibly — please read first

This tool exists for **personal, non-commercial use**, by individuals who have a
genuine scheduling pressure (visa deadline, work conflict, exam-knowledge
expiry from driving school, etc.) and cannot wait for the website's normally
available slots.

**If your situation is not urgent, please use the booking website directly and
take a later slot.** Tokyo's exam supply is structurally constrained, and using
automation just to grab earlier slots ahead of others turns the queue into a
zero-sum race that hurts everyone. The tool is a shortcut for the urgent — not
a way to skip ahead casually.

**Do not abuse the upstream system:**

- The default polling interval is **5 minutes** (per `ADR-3`). Don't lower it.
  Configurable down to 5 min only; faster polling triggers a hard ADR review.
- Run **at most one instance per identity**. The booker is a single-shot state
  machine (per `ADR-6`) — it grabs one slot and stops. Don't bypass.
- **Do not run multiple identities** to maximize your chances. That's exactly
  the kind of behaviour the site's ToS §8.6 prohibits ("複数回 予約登録").
- **Do not redistribute, resell, or commercialize** any output of this tool
  (including booked slots, QR codes, or the tool itself). See LICENSE.

You are entirely responsible for your own use. If your usage causes the
upstream site to throttle, block, or otherwise act against you, that is on
you — not the project. The same applies if it ever leads to an ToS-breach
sanction from the Tokyo Metropolitan Police Department.

**The project respects the site's 利用規約** (Terms of Service) at
`license-test.tokyo-madoguchi-yoyaku.com`. The 8 禁止事項 of Article 8 are
the design constraints — see `DECISIONS.md#ADR-5` for the full review.

---

## 🇯🇵 Why Tokyo only? Why won't this expand to other prefectures?

Tokyo's 3 試験場 (府中 / 鮫洲 / 江東) routinely have a 1+ month booking queue
for 学科試験. **No other Japanese prefecture has anything close to this
scarcity** — most regions can be booked within days from the website.

If you are flexible on location and your driving-school certificate is still
valid (1 year window), **just go take the test in another prefecture.** It is
the structurally correct fix, not this tool. See `OBSERVATIONS.md §結構的不足`
for the rationale.

For these reasons the project is **scoped to Tokyo only and will not expand**.
A v2 cross-prefecture *router* (recommend the earliest available slot across
all 13 候補) would be a different project entirely, and would need different
operational constraints (rate limits across multiple prefectural endpoints,
session policy review, etc.).

---

## Scope of this implementation

Currently this tool **only handles the 学科試験 (written exam,
`coursecode=11` = KYOSOTSU)** flow under the umbrella entry page
`license-renew/index_000.html`. The same Tokyo MPD reservation system also
covers two adjacent flows:

| Flow | Radio button | coursecode | Status |
|---|---|---|---|
| 学科試験 (post driving-school written exam) | `licensetest` (M336) | `11` | ✅ Implemented |
| 仮免許学科試験 (provisional permit written exam) | `provisional` (M335) | (not mapped) | ❌ Not implemented |
| 路上試験 / 技能試験 (driving practical) | (separate sub-system) | (different) | ❌ Not implemented |
| 免許更新 (license renewal) | `licenserenew` (M334) | (different) | ❌ Not implemented |

These adjacent flows are likely reachable through the same upstream API host
with different `coursecode` values, but **the schema, fullwidth-conversion
rules, and ToS-compliance reasoning have only been validated for 学科試験**.
The tool is intentionally scoped to that single flow because:

- That's the flow that has the supply-vs-demand crisis in Tokyo.
- Single-scope means smaller surface area for ToS compliance review.
- Personal-use rationale: the author only needed 学科試験.

**Contributions extending to other flows are welcome**, provided the
contributor:

1. Re-runs the ToS analysis (see `DECISIONS.md#ADR-5`) for the new flow.
2. Captures + documents the upstream schema (in `OBSERVATIONS.md`) before
   writing code.
3. Maintains the single-shot, non-commercial, no-multi-identity-abuse
   posture this project is built around.
4. Does not generalize the tool into a "reservation grabber" for unrelated
   systems — keep it scoped to the Tokyo MPD reservation site.

If you want to extend, please open an issue first to align on scope.

---

## What it does

A small polling loop that:

1. Every 5 minutes, queries the Tokyo MPD's 学科試験 reservation calendar
   API (`/calgetres`) for your candidate test centers (府中 / 鮫洲 / 江東).
2. Diffs against a local snapshot.
3. When a new open slot appears within your acceptable date range:
   - Sends you an email immediately (with how-to-book instructions and a
     direct entry-page link).
   - **Optional**: also pushes to your iPhone via [Bark](https://bark.day.app)
     — set `DL_RES_BARK_URL` in `.env.local` (see `.env.example`).
   - **Optional**: auto-submits a booking via `/putres` (single-shot, with
     a mandatory dry-run first; see `DECISIONS.md#ADR-5`/`ADR-6`).
4. After a successful booking:
   - Persists the full upstream response (`state/booked-confirmation.json`).
   - Emails you the QR code (extracted heuristically) + receipt number +
     reservation number, as multipart attachments + a JSON loss-less audit
     copy.
   - **Stops alerting on new slots** — you already have one.

Heartbeat: if no slot is in your window for 24 hours, you get one "still
alive" digest so a dead cron doesn't go unnoticed.

---

## 🚀 Quick start (with help from Claude / GPT / Cursor / etc.)

If you are **not a developer** and not comfortable with terminal / Python /
launchd setup, that's fine — modern AI coding assistants can walk you through
this. Open Claude Code (`claude.ai/code`), ChatGPT, Cursor, or similar and
copy-paste this prompt:

> I want to set up the `tokyo-dl-reservation` tool from
> https://github.com/NYTC69/tokyo-dl-reservation on my macOS machine. Please
> walk me through, one small step at a time:
>
> 1. Cloning the repo and installing Python 3.11+ and `uv`.
> 2. Running `uv sync` and `uv run pytest` to verify the install.
> 3. Setting up **my own** SMTP credentials so the tool can email me — NOT
>    using anyone else's credentials. I want my own Gmail / Outlook / Yahoo /
>    Fastmail / etc. account. Help me create an app-specific password and
>    fill in `.env.local` (template at `.env.example`).
> 4. Creating my own `config.local.json` from `config.example.json` —
>    candidate places, license type, and my acceptable deadline date.
> 5. Filling in `.env.local` with my own `DL_RES_BOOKER_*` identity fields
>    (name in fullwidth, gracer_no, phone, birthday) **only if** I want
>    auto-booking. If I just want monitoring + email alerts, skip these.
> 6. Running a one-shot dry test with `uv run dl-poll --config
>    config.local.json --silent-baseline` to make sure email lands.
> 7. Setting up the launchd agent from `RUNBOOK.md` for 5-minute polling.
> 8. Confirming the **first run is dry-run only** — I want to manually
>    review the payload it would submit before unlocking real booking with
>    `--book-real`.
>
> At each step show me exactly what to type, what to expect to see, and
> stop and let me confirm before moving on.

The AI assistant will then guide you through it interactively. Total time
~30–60 minutes depending on your familiarity with the terminal.

**Important — bring your own SMTP credentials.** This project intentionally
does not ship a hosted email-sending service. You must use your own email
account so:

- You receive notifications privately (your alerts don't pass through
  anyone else's inbox).
- You don't share credentials with strangers (which is also a violation of
  most providers' ToS).
- The original author's email is not used — that account is private and
  cannot relay your email traffic.

For Gmail specifically, use an **app-specific password** (not your real
password): https://support.google.com/accounts/answer/185833.

---

## 📁 What's in this repo

| File / dir | Purpose |
|---|---|
| `src/dl_reservation/` | The Python package (poll loop, booker, notifier). |
| `tests/` | Pytest suite — 67 tests covering every code path. |
| `scripts/probe_calgetres.py` | One-shot probe to verify upstream is reachable. |
| `scripts/run_poll.sh` | launchd / cron wrapper that loads `.env.local` then `dl-poll`. |
| `config.example.json` | Template for your `config.local.json`. |
| `.env.example` | Template for your `.env.local` (SMTP + booker creds). |
| `RUNBOOK.md` | macOS launchd setup, incident playbooks. |
| `ARCHITECTURE.md` | How code is organized. |
| `DESIGN.md` | What the system is supposed to do (intent specs). |
| `DECISIONS.md` | ADR log — the *why* behind each design choice. |
| `OBSERVATIONS.md` | Facts about the upstream API + ToS analysis. |
| `LEARNINGS.md` | Rules derived from incidents (per the `compass` convention). |
| `STATUS.md` | Current version + known issues. |
| `ARTIFACTS.md` | Pointers to project data assets. |
| `.compass/results/` | Reconnaissance JSON snapshots (API schemas, etc.). |

The project follows the [`compass`](https://github.com/NYTC69/compass)
documentation convention.

---

## 🔒 What this repo does NOT contain

- **No real personal data.** All test fixtures use safe placeholders
  (`name=ＹＡＭＡＤＡ`, `phone=09000000000`, `birthday=19000101`,
  `gracer_no=000000000000`). Replace them with your own values in
  `.env.local` (which is `.gitignore`-d).
- **No credentials of any kind.** SMTP and booker identity fields live in
  `.env.local` only.
- **No private session state.** `state/` is `.gitignore`-d.

---

## 📜 License

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/)
— see `LICENSE`.

In one sentence: **anyone may use, modify, and share this code for personal
or non-commercial purposes**; commercial use is not permitted. The license
includes a strong "AS IS / NO WARRANTY" clause — you assume the risk of any
consequence of running it.

This is *source-available*, not OSI-approved "open source", because the
non-commercial restriction is incompatible with the OSI definition. That
trade-off is intentional and aligns with the structural-imbalance rationale
above.

---

## 🤝 Contributing

Bug reports and small fixes are welcome via GitHub Issues / PRs. The project
is intentionally scoped (Tokyo-only, single-shot, single-prefecture) and
**will not accept feature additions that broaden the scope** — the scope is
the design.

If you want to extend cross-prefecture routing or other regions, please fork
under a different name, and **review the upstream site's ToS for that
prefecture independently** before publishing.

---

*This project was developed with the assistance of [Claude Code](https://claude.ai/code).*
