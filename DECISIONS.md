# DECISIONS — DL Reservation

ADR-style. Each entry is immutable once accepted. Supersede with a new
entry; never edit history.

---

<!--
Entry template (copy as you add each new ADR):

### ADR-<N>: <short title>

<!-- synced: <YYYY-MM-DD> drawer-id=<id> sidecar-hash=<hash> target=<wing>/<room> schema=v1 -->

- **Date**: YYYY-MM-DD
- **Status**: Proposed | Accepted | Deprecated | Superseded by ADR-<M>
- **Context**: what problem / constraint forced this choice
- **Options considered**: A / B / C with one-line trade-offs each
- **Decision**: the chosen option
- **Consequences**: what we gain, give up, take on as future cost

NOTE: The `<!-- synced: ... -->` line is written by `hooks/sync-decisions.py`
+ the drain protocol (per SPEC §SSOT files → passive loading via mempalace).
Do NOT hand-author it on a new ADR — leave the slot empty; the next Stop
hook run + drain will populate it. Hand-edits to existing stamps will be
detected as drift on the next Stop and corrected.
-->

### ADR-1: v0 抓取层使用直接 JSON POST,不引入 headless browser

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_73731b4ac286f1de sidecar-hash=3043f8a8ce51f95efc710e02dfc694f2 target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Accepted
- **Context**: 目标站点
  `license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/01/html/main.html`
  是 jQuery SPA,可观测的预约入口表面看像必须用浏览器。但
  `MKAYMA001data.js` / `MKAYMA001senddata.js` 显示所有数据查询/写操作
  都打到独立 API host(`license-test-tokyo-prd-police-pref-api...`)的
  JSON 端点(`getres` / `calgetres` / `putres` / `cancel` / `customerops`),
  无登录、无 CSRF、无 captcha,服务器只校验 client time 漂移
  ≤ 86400s。我们 v0 仅需只读轮询(`calgetres` / `getres`),不需
  下单也不需保持任何 session。
- **Options considered**:
  - A. **直接 `requests` POST 到 JSON 端点** — 实现简单(单文件即可),
    资源占用低(每次轮询 ms 级),易于在轻量 cron / launchd 跑;但
    依赖前端 JS 反推的 endpoint 与 payload 形态,站点改版时会断。
  - B. **headless browser(Playwright / Selenium)** — 通过真实点穿
    流程更稳健,但启动成本 ~1s+ 每次、内存几百 MB,本质不必要;
    且对 v2 web 化部署不友好。
  - C. **hybrid:Playwright 录一次流量再生成 stub,平时用 stub** —
    维护成本最高,本质是 A 的工程化包装,v0 阶段过度工程。
- **Decision**: A — 直接 JSON POST。
- **Consequences**:
  - + v0 实现可压在单文件 ~100 行 Python。
  - + 资源占用低,每日全量轮询成本可忽略。
  - + 易于打包为 v2 后端 worker(无浏览器依赖)。
  - − 站点前端 / API 改版时会断,需要预留"抓取失败 → 通知作者"
    的 fail-loud 路径(已在 DESIGN.md §Edge cases 列出)。
  - − 必须自我节流(无登录态意味着对方没有用户级限流可参照),
    起步用 5-10 分钟/次,根据实测调整。

### ADR-2: v0 个人轮询节流 — 起步 10 分钟/次,v2 商业化前必须取得 MPD 同意

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_36da6f9dd010352d sidecar-hash=f139543c4e5f9d45331e9460d857ed4e target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Superseded by ADR-3
- **Context**: 站点 `tokyo-madoguchi-yoyaku.com` 的「利用規約」(嵌入
  在 `MKAYMA001MessagesJa.js` `MKAYMA00M016` 中,2025-03-04 施行)对
  自动化访问没有明文规定频率,但相关条款为:
  - **Article 7**:24/365 原则开放,可不预告维护。
  - **Article 8(4)**:未经警视厅事前同意,禁止以「宣伝・広告・勧誘
    又は営業活動」目的使用本系统 — **直接约束 v2 SaaS 形态**。
  - **Article 8(8)**:禁止「不当干扰系统运营、侵害其他用户权利或可
    能造成此类影响」的行为 — v0 自动轮询的唯一硬约束。
  - **Article 9**:个人信息仅限予约登録目的 — v0 完全不输入个人信息
    (只读 `calgetres`),合规。
  没有 robots.txt(实测 404 fallback);唯一可解释的「节流上限」由
  Article 8(8) 的"不显著超出真实用户使用强度"推导。
- **Options considered**:
  - A. **5 分钟一轮**:6 (place,course) × 2 个月 = 12 req,平均 2.4
    req/min。略高于一个活跃用户的瞬时强度但仍很低。优点:发现新空缺
    更快;缺点:更接近"显眼"边界。
  - B. **10 分钟一轮**:同上但平均 1.2 req/min,远低于一个真实用户
    在使用窗口期内点穿日历的瞬时强度。优点:留出充分余量;缺点:
    一个空缺最长可能要 10 分钟才被发现,而被别人秒抢的窗口可能更短。
  - C. **不轮询,仅事件触发**:站点没有 push 通道,不可行。
- **Decision**: **B(10 分钟一轮)作为 v0 起步**。一周后若实测无限流
  迹象且空缺存活时间显著 < 10 分钟,再考虑收紧到 5 分钟。**任何更密
  的频率(< 5 分钟)需新一条 ADR 重新评估**。
- **Consequences**:
  - + 在 Article 8(8) 的软约束里留出充分余量。
  - + 单日总请求量 ~1700,对该站点完全可忽略。
  - − 极快被秒抢的空缺(< 10 分钟存活)会被错过 — 实测后视情况调整。
  - − **v2 SaaS 上线前必须按 Article 8(4) 取得警视厅同意**,这是
    硬阻塞,不只是我们这边的合规姿态问题。届时另开一条 ADR 决定
    "走申请"还是"产品形态调整为不被 8(4) 触发"(例如不收钱、纯
    OSS 工具)。

### ADR-3: v0 轮询节流收紧到 5 分钟一轮(supersedes ADR-2)

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_9f5b578ac7894585 sidecar-hash=db097fd5c2642d9657fb08bb7e9ef241 target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Superseded by ADR-8
- **Supersedes**: ADR-2
- **Context**: ADR-2 选 10 分钟一轮作为起步,留出"一周后实测无限流再
  收紧"的下探空间。用户在 v0 上线当天 review 时反向提出:既然 1.2
  req/min 远低于真实用户瞬时强度,边际再多一档(0.6 → 0.3 req/min)
  收益有限,跳过逐档下探,直接定 5 分钟。
- **Options considered**:
  - A. **维持 10 分钟/轮**(ADR-2 现状) — 安全余量大,但每个空缺最长
    需 10 分钟才被发现。
  - B. **5 分钟/轮** — sustained 0.6 req/min,仍远低于真实用户瞬时
    使用强度,空缺发现延迟减半。
  - C. **逐档下探(10 → 5 → 2 → 1 分钟)** — 信息收益最大但操作成本
    最高,且每档都得改 plist + reload + 留下解读空间。
- **Decision**: **B(5 分钟一轮)**,作为 v0 长期工作频率而非中转档。
  **任何 < 5 分钟的频率仍需新一条 ADR**(ADR-2 §Decision 末段约束
  保留)。
- **Consequences**:
  - + 空缺发现延迟从最长 10 分钟减半到最长 5 分钟。
  - + sustained 请求强度仍远低于站点对单个真实用户的瞬时容忍度,
    Article 8(8) 软约束仍有充分余量。
  - − 一周后即便实测平稳,也不会自动再收紧到 2 / 1 分钟,需要新 ADR
    走一次评估流程。这是刻意的 — 让"更密"必须经过显式决策。
  - − 仍受 ADR-2 中 Article 8(4) 对 v2 SaaS 形态的硬阻塞约束(本 ADR
    不超出 v0 个人使用范围,不影响该约束)。

### ADR-4: 通知契约 — 立即邮件 + 24h heartbeat

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_fcf83c3b9b14fe25 sidecar-hash=20f2be363f7c192a6aac11a3a15cffc5 target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Accepted
- **Context**: 当前实现在 `notifier.py` 上有一个隐性问题:
  `EmailNotifier.notify(empty)` 直接 no-op,加上 `latest_acceptable_date`
  深度滤过后窗口可能长期为空(比如全部 slot 当前 res==cap 且无新增),
  系统会**完全静默**——用户无法区分"cron 死了"和"今天没新空"。
  反过来,如果取消 deadline 滤,窗口里 1 个月以后的远期 slot 又会持续
  存在,每次诊毫无信号意义。需要一条新的通知契约同时解决这两端。
- **Options considered**:
  - A. **每次 poll 都发邮件**(无论是否新空) — 噪音爆炸,1 天 288 封,
    用户会自动忽略,等于没用。
  - B. **沉默 + 一个外部 watchdog** — 用 launchd 的 `KeepAlive` 或外部
    监控判断 cron 死活。多一层基础设施,过度工程,且不告知用户"窗口
    内还没空缺"这一业务语义。
  - C. **24h heartbeat 兜底**(本 ADR) — 立即邮件保留(diff 检测 + 有
    空座的 slot 才发,现行逻辑),额外加一条:**deadline 窗口内 0 个
    可订 slot 且距上次 heartbeat ≥ 24h 时,发一封"暂时没有想要的预约
    时间"摘要邮件**。
- **Decision**: **C**。具体语义:
  - "可订" = `slot.is_open`(`capacity > reservation`)且 ≤
    `latest_acceptable_date`。
  - heartbeat 触发条件:`not any(s.is_open for s in relevant)` 且
    `now - last_heartbeat_at ≥ 24h`。
  - heartbeat 状态文件:`state/heartbeat.json`(snapshot.json 同目录
    sibling),只含 `last_heartbeat_at`(ISO-8601 UTC)。独立于
    snapshot 是为了让"清快照重新 baseline"操作不会误触 heartbeat
    时钟。
  - silent baseline 首跑期间 heartbeat 也被抑制,理由同 notify。
  - 间隔 24h 在 `heartbeat.HEARTBEAT_INTERVAL` 硬编码,v0 不暴露给
    config;v1 可考虑做成可配。
- **Consequences**:
  - + 用户至少每 24h 收到一封邮件确认 cron 在跑(若窗口空)。
  - + 一旦窗口里出现可订 slot,立即邮件路径仍优先触发,不被 heartbeat
    噪音淹没。
  - + heartbeat email subject 含 deadline,直观;body 含监视范围统计,
    便于用户发现 deadline 配错的情况。
  - − 引入一个新 state 文件 `state/heartbeat.json`,需要在 RUNBOOK
    "重置" 章节同步说明。
  - − 若 SMTP 凭证配错,heartbeat 与 notify 一起失效(不会因为只有
    heartbeat 单独熄火)。这是预期行为 — heartbeat 应与 notify 共享
    凭证健康度。

### ADR-5: v1 个人自动代下单合规通关(supersedes DESIGN §Non-goals "v0 不代下单")

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_c7b4b2dbfdfd4e32 sidecar-hash=c2f6308c653d39d704823d0935ad7b0e target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Accepted
- **Supersedes**: DESIGN.md §Non-goals (v0 阶段"不代下单"那一条) — v1
  起代下单进入产品范围。ADR-2/3/4 不受影响。
- **Context**:
  - v0 上线当天即在生产数据中观测到 race-loss(2026-05-08 21:52 邮件
    通知 5/15 0800 1 个空座 → 用户 21:55 打开网站 0 个空座,3 分钟内
    被抢)。手动响应链路(收件 → 看 → 点 → 选 → 提交)无法在秒-分级
    赛跑中胜出,这是 v0 设计上的硬上限。
  - 用户决议加速 v1(原 BACKLOG P2 milestone),前置阻塞项是"利用規約
    第 8 条全 8 款 + 关联条款逐条 review,确认是否明文禁止自动化下单"。
  - 本 ADR 完成 review,详细条款摘要落 OBSERVATIONS.md §利用規約。
  - 加分上下文(社会信号):用户教習所老师明示鼓励 AI 工具订位 — 不
    替代条款分析,但说明实践已在该社群默认接受。
- **Options considered**:
  - A. **v0 不动,v1 自动下单上线** — 个人非商业用途,逐条 review 后
    没有明文禁止;接受 4 项 ToS 衍生设计约束。
  - B. **维持 v0 仅通知,放弃 v1** — 安全姿态最高,但 race-loss 无解;
    用户的 5/20 ddl 在风险中。
  - C. **走警视厅事前同意通道** — 仅 v2 SaaS 商业化必走,个人 v1 不
    需要(8.4 条仅约束営業目的)。过度合规,放到 v2。
- **Decision**: **A — v1 上线代下单,接受以下 4 项 ToS 衍生设计约束**
  (这 4 条是从 ToS 第 6/8/9/11 条直接推导出的 hard rule,代码层
  必须落地):
  1. **不持双订(8.6 + 6.4)**:swap 流程**必须**是
     `cancel(old) → wait_ack → book(new)`,绝不**先 book 后 cancel**。
     中间状态 fail = 用户失去 old slot 但未拿到 new(可接受 trade-off,
     回退路径是回到通知模式让用户手抢)。**禁止任何情况下持有 ≥2 个
     未来活动予約**。
  2. **零错填(11.3)**:每个新启用自动下单的用户,首次必须强制
     **dry-run** 一次 — 把完整 putres payload + 解码后的字段集打印
     到日志/邮件给用户人眼确认,确认通过后才解锁真发模式。错填导
     致当天不能受験是 ToS 明文罚则,不是软风险。
  3. **写端点退避(8.8)**:`putres` 失败的重试**必须**用指数退避
     (1s → 3s → 9s,最多 3 次),不连发;HTTP 4xx 不重试(配置错,
     重试无用);HTTP 5xx 走退避;`code != A0001` 视为永久失败,
     立即停并通知用户。
  4. **个人信息边界(9)**:用户凭据(姓名 / 生年月日 / 仮免許番号 /
     连絡先)只能用于**该用户自己的** putres 调用;**不写日志**(连
     redact 后也不写,杜绝 forensic-leak);存储位置选 macOS Keychain
     而非 .env.local(Keychain 有 OS 级 ACL,.env.local 只有文件权限)。
     **NOTE 2026-05-08**: §4 的 Keychain 部分被 **ADR-7** supersede 给 v1
     single-user 场景;v2 多用户场景仍需重新评估。
- **Consequences**:
  - + race-loss 闭环:auto-book 在 5min 轮询 + 邮件链路上把人肉环节
    去掉,响应时间从分钟级压到秒级。
  - + 4 条设计约束直接提供 v1 实现的 acceptance criteria,不靠"事后
    review"约束代码质量。
  - + 教習所側默认接受 + ToS 无明文禁止 = 合规姿态成立,可以公开讨
    论代码,不必"low-key 偷跑"。
  - − 8.6 条"正当な理由なく複数回 予約"是软词,如果未来警视厅扩
    展解释覆盖"自动 swap 行为",我们 swap 流程会暴露在风险下。约束
    1(不持双订 + cancel-first)是这条的兜底。
  - − v1 凭据存储升级到 Keychain 是新基础设施工作,实施前需评估
    macOS-only 约束(Linux 用户 fallback 是 ?)。v0 是 macOS-only
    项目,本期不需要 Linux,延后讨论。
  - − Article 8.4 对 v2 商业化的硬阻塞**仍然成立**;本 ADR 仅解锁
    v1 个人用例,不影响 ADR-2/3 中关于 v2 SaaS 必走警视厅同意的
    结论。

### ADR-6: v1 状态机 — single-shot booker(no swap, no rebook-improve)

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_5f85b9ff1cbb5b5c sidecar-hash=2fd53ad24ad8cae5577236c5c7670942 target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Accepted
- **Context**: 用户 2026-05-08 对 v1 设计明确表态:"一个人确实只需要
  预约一个, 不会想要预约两个时间的"。这把 ADR-5 §1 中"swap 流程必须
  cancel-first"那块复杂度直接消除 — v1 不实现 swap,不监视"是否有
  更早的 slot",抢到一个就停。
- **Options considered**:
  - A. **single-shot**(本 ADR):成功 book → 写"已预约"标记 →
    **后续 poll 仍跑(用于 v0 通知用户"slot 已被订上"或检测 booking
    被 cancel),但不再触发 booker**。最简单、最可证安全(8.6
    constraint trivially satisfied)。
  - B. **swap-on-better**:成功 book 后继续监视,有更早 slot 就
    cancel 旧 → book 新。需要 cancel 与 book 的 ack 时序、
    failure-during-swap 的回滚、用户配置"什么算 better"。复杂度高
    一个量级。
  - C. **保持 v0 仅通知,不做 v1 booker**:不解决 race-loss,
    用户 5/20 ddl 暴露在风险下。
- **Decision**: **A — single-shot booker**。状态机:
  - **WATCHING**(默认):每 5 分钟 poll → 有新 `is_open` slot 触发
    booker.try_book() → 成功 → 转 BOOKED。
  - **BOOKED**:state 文件含 `booked_slot` 记录(date / starttime /
    place / course / 受付番号)。poll 仍跑,但 booker 短路(不再尝试
    book 任何 slot),通知器仅在"slot 仍存在"时静默,在"已预约的
    slot 状态异变"(被取消 / 被人订没了)时立即报警。
  - 退出 BOOKED 路径**只有**用户手工 reset(`rm state/booked.json` 或
    新增 `dl-poll --reset-booking`)。系统不会自动从 BOOKED 回到
    WATCHING。
- **Consequences**:
  - + ADR-5 §1 "禁止持双订" trivially 成立,**不再需要 cancel-first 时序
    + ack 等待 + 回滚** — 这部分代码 / 测试 / ADR 论证全部省掉。
  - + 用户语义清楚:"工具的工作 = 抢到一个考试位置",抢到就完。
  - + dry-run UX 简化:首次成功(真发)路径只走一次,之后就不再发
    putres,审计简单。
  - − 抢到不理想 slot 后想换:只能用户自己上网手 cancel + 删 booked
    state + 等下一轮。是 trade-off 不是缺陷,与"single-shot 最简"目标
    一致。
  - − 若 booking 被对方系统单方面 cancel(比如 7-day 维护误清),
    用户需要手动 reset 工具才能重启 booking。可接受(罕见 edge case)。

### ADR-7: v1 booker 凭据存 .env.local 而非 Keychain(supersedes ADR-5 §4)

<!-- synced: 2026-05-08 drawer-id=drawer_3cats_decisions_dl-reservation_9394c75e2c50654f sidecar-hash=69b6d75625eb9b194fa09d90759e3696 target=3cats/decisions_dl-reservation schema=v1 -->

- **Date**: 2026-05-08
- **Status**: Accepted
- **Supersedes**: ADR-5 §4(仅"存储位置选 macOS Keychain"那部分;
  其余 §4 约束 — "不写日志"、"redact 也不写" — 仍生效)
- **Context**:
  - ADR-5 §4 论证了 Keychain 优于 .env.local 的安全 delta(OS-level
    ACL vs 文件权限),用于 v2 多用户硬化的合理姿态。
  - 用户 2026-05-08 review v1 实现时明确 override:
    > "你帮我把我的一些个人信息相关的落到.env.local 里直接从环境变量
    >  里读取, 去做预约。"
  - 本项目 v1 是 single-user single-machine,用户对自己机器上 .env.local
    的暴露面已 informed-accept(本会话早些时候用户也表示"我不介意这个
    数据泄露, 问题不大")。
  - SMTP 密码已在 .env.local 沿用同一模式跑通;凭据加进同一文件 = 一
    致性强、零新基础设施、`scripts/run_poll.sh` 现有 source 逻辑
    直接复用。
- **Options considered**:
  - A. **保持 ADR-5 §4 Keychain 路径** — 安全姿态最高,但要写 30 行
    `subprocess + security` 包装 + 用户首跑要跑 `dl-credentials set`
    才能解锁。被用户 explicit override。
  - B. **混合:Keychain 优先,env 兜底** — 灵活,但代码复杂度 ×2,
    维护负担长期化。
  - C. **.env.local 单一路径**(本 ADR):4 个 env var
    `DL_RES_BOOKER_*`;`scripts/run_poll.sh` 已 source `.env.local`,
    自然 inherit 进 dl-poll process。
- **Decision**: **C — .env.local + 4 个 `DL_RES_BOOKER_*` env var**。
  - `DL_RES_BOOKER_NAME`(fullwidth Latin/Katakana)
  - `DL_RES_BOOKER_BIRTHDAY`(YYYYMMDD half-width)
  - `DL_RES_BOOKER_PHONE`(11 digits half-width)
  - `DL_RES_BOOKER_GRACER_NO`(half-width 仮免許番号,booker 写时
    自动转 fullwidth)
  - `credentials.load()` 4 个全有 → 返回 BookerCredentials;任一缺 →
    抛 `CredentialsNotFound`,booker 自动禁用,poll 仅做通知。
- **Consequences**:
  - + 用户首跑只需:在 `.env.local` 加 4 行 + 加 `--enable-booker` 进 plist
    → 立即可用。
  - + ADR-5 §4 其他约束(不写日志、`booker.py` 不持久化、不在异常
    message 里 echo 真值)**仍然生效**,只是 storage layer 妥协。
  - − v2 多用户场景这条妥协**不能延续** — env-based credentials 在
    multi-tenant 进程下意味着所有租户共享凭据。届时回归 Keychain 或者
    更高强度的 KMS。
  - − .env.local 文件权限是 600(文件级),如果用户的 macOS 账号被
    入侵,凭据明文可读。Keychain 在同一场景下需要额外的 GUI prompt
    才能解锁。这是用户已 informed-accept 的 trade-off。

### ADR-8: 轮询节流收紧到 3 分钟一轮(supersedes ADR-3)

- **Date**: 2026-09-17
- **Status**: Accepted
- **Supersedes**: ADR-3
- **Context**: ADR-3 把 5 分钟定为 v0 长期工作频率,并保留 ADR-2 的
  约束"任何 < 5 分钟的频率需新一条 ADR"。自 2026-05-08 上线以来按
  5 分钟跑了四个多月,未观察到限流 / 封禁迹象(见 RUNBOOK
  §Incident playbooks 无相关记录)。用户 2026-09-17 要求把检测间隔改
  为 3 分钟,以缩短空缺发现延迟。本 ADR 即 ADR-3 要求的显式评估。
- **Options considered**:
  - A. **维持 5 分钟/轮**(ADR-3 现状) — 零风险变化,但每个空缺最长
    需 5 分钟才被发现。
  - B. **3 分钟/轮** — sustained 请求强度为 ADR-3 的 5/3 ≈ 1.67 倍
    (以 ADR-3 口径 0.6 req/min 计约 1.0 req/min),仍远低于一个真实
    用户点穿日历时的瞬时强度;空缺发现延迟从最长 5 分钟降到最长 3
    分钟。
  - C. **≤ 2 分钟/轮** — 延迟更短,但接近"持续像一个一直刷新的用户"
    的显眼边界,且 ADR-2 §Context Article 8(8) 的软约束余量明显变薄。
- **Decision**: **B(3 分钟一轮)**。`POLL_INTERVAL` / launchd
  `StartInterval` 默认值改为 180 秒;compose 中 `partner` 的
  `START_DELAY` 同步改为 90 秒以维持"错开半个周期"。**任何 < 3 分钟
  的频率仍需新一条 ADR**(ADR-2 §Decision 末段约束保留,阈值下移)。
- **Consequences**:
  - + 空缺发现延迟上限从 5 分钟降到 3 分钟。
  - + 四个月实测数据支持:5 分钟档无任何限流迹象,收紧一档仍在
    Article 8(8) 软约束余量内。
  - − 单日请求量约为原来的 1.67 倍;若出现 429 / 5xx 或
    `CalGetResError` 连续报错,按 RUNBOOK 暂停并回退到 5 分钟。
  - − 多人部署(compose 里多个 service)时叠加强度更高;每加一个
    实例都应重新核对总请求强度,不能默认继续收紧。
  - − 仍受 ADR-2 中 Article 8(4) 对 v2 SaaS 形态的硬阻塞约束(本 ADR
    不超出个人使用范围)。
