**Last updated**: 2026-05-08T18:50

## Deployment topology

- Frontend: 无(v0 是 CLI 脚本)
- Backend: 单机 Python 包 `dl_reservation`(`src/dl_reservation/`),
  通过 `dl-poll` 入口或 `python -m dl_reservation.poll` 调用。
- Database: 无 — 状态持久化到本地 JSON 文件(默认 `state/snapshot.json`,
  通过 `--state` 覆盖)。
- Background workers / cron: macOS launchd 或 cron(v0 部署在作者的
  research 机器上)。
- Credential / secret locations: SMTP 凭证(Gmail 应用专用密码)放
  仓库根 `.env.local`(权限 600,gitignored)。`scripts/run_poll.sh`
  在调用 `dl-poll` 前 `source` 它,程序通过 `os.environ` 读取,代码
  里没有任何明文凭证。生成 app password:
  https://myaccount.google.com/apppasswords(需要先开 2FA)。

## Start / Stop / Restart

```bash
# 首跑(silent baseline)— 把当前所有开放写入 snapshot 但不通知,
# 防止启用通知后第一轮把存量当新增刷屏:
./scripts/run_poll.sh --silent-baseline

# 之后日常手动跑:
./scripts/run_poll.sh

# wrapper 会自动 source .env.local 再调用 dl-poll;真正的定时跑见
# §Scheduled jobs。
```

### v1 booker 启用流程(ADR-5 §2 强制 dry-run 优先)

```bash
# 0) 在 .env.local 补 4 个 booker env var(参考 .env.example)。
#    至少要 DL_RES_BOOKER_NAME / BIRTHDAY / PHONE / GRACER_NO 全有。
#    NAME 必须全角(片仮名或全角 Latin)。

# 1) Dry-run 一次 — 看到一封 "🧪 DRY-RUN payload review" 邮件,
#    人眼检查 name / gracer_no 全角是否对、slot 是否预期。
./scripts/run_poll.sh --enable-booker

# 2) 如果 dry-run 邮件 OK:把 plist 改成下面这行,reload。
./scripts/run_poll.sh --enable-booker --book-real

# 3) 后续看到 "🎉 予約成立" 邮件 = booking done。state/booked.json
#    被写,booker 进入 BOOKED 状态,后续 poll 不再尝试新 booking。

# 4) 取消 booking — 两种路径:
#    a) 已经在网站上手动 cancel 了,只想清本地 state:
./scripts/run_poll.sh --reset-booking
#    b) 想一步到位,通过 API 取消 + 清本地 state:
./scripts/run_poll.sh --cancel-booking
#    --cancel-booking 用 .env.local 里的 booker 凭据调上游 /cancel,
#    成功后才删 state/booked.json。失败保留 state 让你 debug。
#    c) 一键取消(手机):.env.local 填 DL_RES_CANCEL_TOKEN(随机串)+
#       DL_RES_CANCEL_URL(Tailscale 地址,如 http://mac.tailnet.ts.net:8787),
#       `docker compose up -d cancel-me`。之后"予約成功"的 Bark 推送
#       点开 → 页面按"予約を取消" → 走的就是 b) 的同一流程。
```

**ADR-6 single-shot 状态机**:WATCHING(无 `state/booked.json`)→ 抢
到任意 slot → BOOKED(`state/booked.json` 写入)→ 仅靠用户手动
`--reset-booking` 退出。booker 永远不会自动持有第二个 booking。

停止 = 删除 launchd / cron entry。无后台守护进程,kill 单次 `dl-poll`
进程即可。

## Deploy procedure

v0 阶段无远程部署,只在作者本机:

```bash
git pull
uv sync          # 同步依赖到 .venv
uv run pytest    # 烟囱测一下
```

## Common ops

- Tail logs: `tail -f /tmp/dl-reservation.log`(假设 launchd plist 把
  stdout 重定向到这里,详见 §Scheduled jobs)。
- DB shell: 不适用(JSON 快照 = `cat state/snapshot.json | jq .`)。
- 清缓存 / 重置:
  - `rm state/snapshot.json` — 重 baseline。下次跑若不带
    `--silent-baseline` 会把所有当前可订 slot 当作新增邮件发。
  - `rm state/heartbeat.json` — 让 heartbeat 时钟立即可发(下一次
    deadline 窗口空时)。两个文件**互不影响**,可单独删。
- Trigger backfill: 不适用,无回填概念。

## Incident playbooks

### Incident: calgetres 持续返回非 A0001 / HTTP 非 200

- **Trigger**: cron 每次跑都报 `CalGetResError` 或 httpx 异常。
- **Diagnose**:
  1. 手动跑 `uv run scripts/probe_calgetres.py`,看是哪一对 (place,
     course) 在报错。
  2. 浏览器打开 `https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/01/html/main.html?lang=ja`
     看站点本身是否在线 / 是否改版。
  3. 若站点在线且能正常下单,但我们 API 调用失败 → 大概率是站点对
     裸 HTTP 客户端做了反爬升级(JS 挑战 / Cookie / Header 校验)。
- **Recover**: 暂停 cron 直到根因明确;不要不断重试加重对方负担。
- **Postmortem**: 若是站点改版,记入 LEARNINGS + 重新调研 schema;
  若是反爬升级,可能需要重写为 Playwright(回到 ADR-1 选项 B)。

### Incident: 通知刷屏 / 重复

- **Trigger**: 同一 (date, place, course, starttime) 连续多次被通知。
- **Diagnose**: 检查 `state/snapshot.json` 是否在 cron 运行账号下可
  写;若每次跑都因写权限失败而无法持久化,diff 会一直把"现存的所
  有开放"判为新增。
- **Recover**: `chown` / `chmod` 修权限,或换一个 `--state` 路径。

## Scheduled jobs

v0 推荐 macOS launchd(research 机器是 macOS)。示例 plist
`~/Library/LaunchAgents/com.example.dl-reservation.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.example.dl-reservation</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/path/to/dl-reservation/scripts/run_poll.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/dl-reservation</string>
  <key>StartInterval</key><integer>300</integer>  <!-- 5 分钟,ADR-3 supersedes ADR-2 -->
  <key>StandardOutPath</key><string>/tmp/dl-reservation.log</string>
  <key>StandardErrorPath</key><string>/tmp/dl-reservation.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
```

注册: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.dl-reservation.plist`
注销: `launchctl bootout gui/$(id -u)/com.example.dl-reservation`

## Access / environment

- 运行环境: macOS(research),Python 3.11+(实测在 3.13 跑 OK),
  uv 管理虚拟环境。
- 网络: 仅出站 HTTPS 到
  `license-test-tokyo-prd-police-pref-api.tokyo-madoguchi-yoyaku.com`。
- 无生产环境概念(v0 即作者本人)。
