# OBSERVATIONS — DL Reservation

Facts about the **external world** that this project interacts with —
APIs, UIs, data quirks, user-group patterns. If a fact is about our own
code, it belongs in ARCHITECTURE / DESIGN / ARTIFACTS instead.

---

<!--
Entry template:

### <short fact title>
- **Task context**: one-line — what work was happening when we learned this
- **Date**: YYYY-MM-DD
- **Fact**: the observation, one paragraph max
- **Anchor**: code constant / external doc link / test that validates it
- **Supersedes**: <old entry title, if replacing one> (optional)
-->

## Units & Invariants

### tokyo-madoguchi-yoyaku 预约系统

- **Task context**: v0 抓取层调研 — 决定是否需要 headless browser。
- **Date**: 2026-05-08
- **Fact**: 站点 `license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/01/`
  是 jQuery SPA,所有数据交互打到独立 API host
  `license-test-tokyo-prd-police-pref-api.tokyo-madoguchi-yoyaku.com`
  的 JSON 端点。无登录、无 CSRF、无 captcha(只读阶段)。
- **Anchor**: `MKAYMA001data.js`,
  `MKAYMA001senddata.js#getReservationTimeList`(包含 `getres` 调用样板)。

### 試験場 placecode(東京)

- `270` 府中試験場 / `280` 鮫洲試験場 / `250` 江東試験場。
- **Date**: 2026-05-08
- **Anchor**: `MKAYMA001placeData.json`(站点静态 JSON,排序由 `sort` 决定)。

### 驾照 type code(`typeDetailNum`)

- 1 YURYOU / 2 IPPAN / 3 IHAN / 4 SHOKAI / 7 KOUREI
- 11 KYOSOTSU(驾校毕业)
- 12 IPPAN-SHIKEN / 13 GENTSUKI / 14 SHOTOKU / 15 NISHU / 21 NINCHI / 31 GAIMEN
- 61 KYOSOTSU-MAINA(驾校毕业 + mynumber 合并)
- **mynumber 合并 → 基础 type 映射**: `detailToBasicCodeMap` =
  `{11:11, 61:11, 12:12, 62:12, 13:13, 63:13, 14:14, 64:14}` —
  即 mynumber 合并版与基础版在 API 共享空席池(待实测确认)。
- **Date**: 2026-05-08
- **Anchor**: `MKAYMA001data.js`(`window.typeDetailNum` /
  `window.detailToBasicCodeMap`)。

### slot 空缺判定

- `getres` / `calgetres` 返回 `body[i]` 含 `starttime` / `endtime` /
  `capacity` / `reservation`。可用 = `capacity > reservation`。
- `calgetres` 一次返回**整月所有 slot 的细粒度数据**(per slot,not per
  day)— v0 轮询只需 `calgetres`,不需要 `getres`。
- 服务器另返回 `currenttime`(epoch float seconds);若
  `|currenttime - client_now| > 86400` 前端拒绝渲染。我们调用时本地时
  钟正常即可,无需伪造。
- **Date**: 2026-05-08
- **Anchor**: `MKAYMA001senddata.js#getReservationTimeList`,
  `MKAYMA001common.js#getVacancyInformation`。

### `GET /calgetres` 实测样例(2026-05-08)

请求: `GET https://license-test-tokyo-prd-police-pref-api.tokyo-madoguchi-yoyaku.com/calgetres?date=202605&coursecode=11&placecode=270&user=pub`

响应(摘):

```json
{
  "currenttime": "1778231720.2528024",
  "code": "A0001",
  "body": [
    {"date":"20260501","starttime":"0800","endtime":"0930",
     "displaytime":"午前試験(受付時間  8:00)従来の免許証",
     "capacity":"103","reservation":"90", "...i18n displaytime_xx 字段..."},
    {"date":"20260501","starttime":"1100","endtime":"1230",
     "displaytime":"午後試験(受付時間 11:00)従来の免許証",
     "capacity":"92","reservation":"85", "..."},
    ...
  ]
}
```

字段说明:
- `code = "A0001"` 表示成功(其他 code 见 `senddata.js` 错误处理)。
- `body[i].date`: `YYYYMMDD` 字符串;`starttime` / `endtime`: `HHMM`
  字符串。
- `capacity` / `reservation` 是字符串形式的整数。
- `displaytime` + `displaytime_en` / `_zh` / `_vi` / `_ne` / `_my` 多
  语言展示文案。
- 前端 `getVacancyInformation` 仅记录每天聚合 `(capacity - reservation)`
  并 floor 到 0;前端 `getReservationTimeList` 渲染时还会把过去时刻
  过滤掉(`date > today_yyyymmdd OR (date == today AND starttime >
  now_hhmm)`)。
- **Date**: 2026-05-08
- **Anchor**: 本机 curl probe(2026-05-08 08:55 JST)。

### `/putres` schema(写端点 — v1 booker 调用)

- **Task context**: ADR-5 / ADR-6 v1 booker 实装。用户 2026-05-08 抓取
  一次成功 booking 的 cURL 给我做 source of truth(7/22 11:00 府中 KYOSOTSU
  → 当晚立即 cancel 释放 slot)。
- **Date**: 2026-05-08
- **Method / URL**: `POST https://license-test-tokyo-prd-police-pref-api.tokyo-madoguchi-yoyaku.com/putres`
- **Required headers**:
  - `Content-Type: application/json; charset=UTF-8`
  - `Origin: https://license-test.tokyo-madoguchi-yoyaku.com`
  - `Referer: .../police-pref-tokyo/01/html/main.html?lang=ja`
  - 标准 `Accept` / `User-Agent`
- **Body schema**(JSON):
  ```
  {
    "date":       "YYYYMMDD",       # slot 日,半角
    "coursecode": "11",             # 11=KYOSOTSU
    "placecode":  "270",            # 270=府中 / 280=鮫洲 / 250=江東
    "starttime":  "HHMM",           # 半角 4 位
    "endtime":    "HHMM",           # 半角 4 位
    "license":    "",               # 空字符串(KYOSOTSU 流程)
    "phone":      "11 digits",      # 半角
    "birthday":   "YYYYMMDD",       # 半角
    "name":       "fullwidth Latin / Katakana",  # ★ 全角
    "gracer_no":  "fullwidth digits"             # ★ 全角(仮免許番号)
  }
  ```
- **关键 gotcha**:
  - **`name` 必须全角**(片仮名 ＹＡＭＡＤＡタロウ 或 全角 Latin
    ＴＡＲＯ ＹＡＭＡＤＡ),半角会被 reject。
  - **`gracer_no` 必须全角数字**(`００００...`),半角输入要先转。
    `booker.py#_to_fullwidth_digits` 做这层转换,用户在 .env.local
    存半角数字即可。
  - `phone` / `birthday` / `date` / `time` / `coursecode` / `placecode`
    全部**半角**。
- **没有 customerops chain**:直接一发 putres 完事,不需要 customer
  注册环节。
- **没有 session / CSRF / Authorization** —— 与 ADR-1 对读端点的判断
  一致延伸到写端点。
- **成功 response**(待用户首次真发后回灌完整 schema):code=A0001 +
  body 含 receipt 类字段。booker.py 已写多个候选 key (`receipt_no` /
  `receiptNo` / `receipt`)做适配兜底。
- **失败 response**: 待回灌(实际 production 失败 case 还没抓)。
- **Anchor**: `src/dl_reservation/booker.py#_build_putres_payload`,
  实测 cURL 锁在 git commit history(已 redact 5 个真值)。

### `/cancel` schema(写端点 — v1 不调,reset-booking 路径备用)

- **Task context**: 同 putres,顺手抓的取消端点。v1 booker 是
  single-shot(ADR-6),自己不调用 cancel;留作未来 `dl-poll
  --cancel-booking` 类命令的 schema reference。
- **Date**: 2026-05-08
- **Method / URL**: `POST .../cancel`
- **Body schema**(JSON):
  ```
  {
    "res_no":     "",                 # 取消用 受付番号(为空时靠下面的身份信息匹配)
    "license":    "",
    "phone":      "11 digits",
    "birthday":   "YYYYMMDD",
    "action":     "3",                # action code 3 = cancel(其他 code 含义未抓)
    "coursecode": "11",
    "name":       "fullwidth",
    "gracer_no":  "fullwidth digits"
  }
  ```
- **观察**:
  - 取消**不需要 slot 的 date/starttime** —— 系统按用户身份(name +
    birthday + gracer_no)+ coursecode 找到那一条 booking。
  - **隐含约束**:这意味着站点 enforce "一人一订" — 如果一个用户身份
    有 2 个未来 booking,这个 cancel API 大概率会有歧义/拒绝。我们
    ADR-6 single-shot 的设计与上游姿态 align,没有 corner case。
  - `res_no` 空字符串,实际填写规则未验。
- **Anchor**: 实测 cURL 锁在 git history。

### API 端点表

- `POST /getres` — 单日 slot 列表(payload:`{date, coursecode, placecode}`)
- `GET  /calgetres` — 月历 slot 列表(query:`date=YYYYMM&coursecode=...&placecode=...&user=pub`)
- `POST /putres` — 下单(写;v0 不调)
- `POST /cancel` — 取消(写;v0 不调)
- `POST /customerops` — 已有预约查询
- `POST /getcrypto` — QR 码加密串
- 静态: `/police-pref-tokyo/01/data/MKAYMA001{placeData,placeCodeSet,filterData,holidayList}.json`
- **Date**: 2026-05-08
- **Anchor**: `MKAYMA001data.js`(`window.getReservationTimeUrl` 等)。

### Robots / 反爬

- 站点 HTML `<meta name="robots" content="noindex,nofollow">`,无显式
  anti-bot 提示;无 captcha;无 CSRF token。仍应自我节流(参考 ToS,
  待 P1 阅读)。
- **Date**: 2026-05-08
- **Anchor**: `main.html` 的 head meta。

### 入口页面 routing(`license-renew/index_000.html`)

- **Task context**: 2026-05-08 用户报告邮件里的深链 `license-test/main.html`
  打开后页面"失效",排查发现 SPA 入口走的是另一个域名。
- **Date**: 2026-05-08
- **Fact**: `https://license-renew.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/index_000.html`
  是**所有 3 个预约流程的统一入口路由页**,含 3 个 radio button:
  - `licenserenew`(M334)= 免許更新
  - `provisional`(M335)= 仮免許学科試験
  - `licensetest`(M336)= **学科試験**(本项目目标)
  用户选 radio → JS 把页面状态初始化并 redirect 到对应子系统的
  `main.html`。**直接深链到 `license-test/main.html?lang=ja` 会绕过
  这一步**,SPA 状态未初始化,页面表现为"无法跳转"。
- **解读**:
  - 域名 `license-renew.*` 名字带"renew"是**误导性命名**,实际它是
    umbrella 入口,3 个子流程都从这里起步。
  - 我们 polling 的 API host(`license-test-tokyo-prd-police-pref-api.*`)
    与 `coursecode=11` (KYOSOTSU) 的对应**正确**,只是 frontend 应
    从 index_000 入,不从 main.html 入。
  - 通知邮件已切换到 index_000 URL + 明确告诉用户选哪个 radio
    (M336 = 学科試験)。
- **Anchor**: `notifier.py#_BOOKING_URL`,`MKAYMA00index000.js`(入口
  路由 JS),`MKAYMA001MessagesJa.js#MKAYMA00M334-336`(radio 文案)。

### 利用規約(MKAYMA00M016,令和7年3月4日施行)

- **Task context**: ADR-5 v1 个人自动下单合规 review。
- **Date**: 2026-05-08(全文抓取)
- **Source**:
  `https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/01/js/MKAYMA001MessagesJa.js`
  → key `MKAYMA00M016`(2678 字符)。
- **关键条款摘要**(全文抓在 git 历史 / probe 脚本日志中,不重复粘贴):
  - 第 1 条 目的:运転免許更新申請 / 仮免許学科試験 / 学科試験受験申請 的
    **予約**。
  - 第 6 条 予約登録 / 変更 / キャンセル:
    - 变更 = 取消旧 → 重订新(没有"原子改"操作)。
    - 取消通过本系统进行,可在予約日当日 当該予約時間直前 まで。
  - 第 7 条 利用時間:24/365 原则;警视厅可不预告维护停机。
  - **第 8 条 禁止事項(8 项,核心)**:
    1. 法令違反 / 犯罪関連
    2. 公序良俗反
    3. **警视厅 / 利用者 / 第三者の プライバシー / 名誉 / 権利 / 利益 を
       侵害する行為**
    4. **警视厅事前許諾なし の 宣伝・広告・勧誘・営業 目的使用**(对 v2
       SaaS 形态硬阻塞)
    5. **虚偽 又は 不正な事実 で予約登録 → QR / 受付番号交付 を受ける**
    6. **正当な理由なく複数回 予約登録 → QR / 受付番号交付 を受ける**
    7. **第三者譲渡・転売 目的の予約登録**
    8. **本システム運営 を不当に妨害 / 他の利用者の権利侵害(又はおそれ)**
  - 第 9 条 個人情報:**予約登録 目的のみ** 利用。
  - 第 11 条(3)受験項目等を**誤って予約登録** → 予約日当日 受験できない
    (硬罚则)。
- **解读**(逐条对 v1 个人自动下单的影响详见 ADR-5):
  - **没有明文禁止"自動的・プログラム的予約申込"**。
  - 8.4 条仅约束**商业用途**(宣伝・広告・勧誘・営業) — 个人使用不触发。
  - 8.6 条"複数回 予約"对 swap 流程是设计约束,不是禁止。
  - 8.8 条对**写端点**的 sustained 速率敏感,但单次响应式 booking 不
    触发(等同于一个真实用户在页面上点"予約確定")。
- **Anchor**:DECISIONS.md#ADR-5(逐条 review),
  `MKAYMA001MessagesJa.js#MKAYMA00M016`。

### 教習所側の社会的シグナル

- **Task context**: ADR-5 review 时来自用户的口头信息。
- **Date**: 2026-05-08
- **Fact**: 用户所属教習所老师**鼓励**学生使用 AI / 自动化工具来订
  这个系统的笔试 slot。**不是**正式条款解释,但说明在该学习者社群
  里这是被默认接受的实践,降低了"我们偷偷摸摸"的伦理/感知风险。
- **Anchor**: 用户口述,无文档锚点。条款解读仍以 ToS 全文为准。

### 結構的不足 — 東京試験場供給と全国分布のギャップ

- **Task context**: ADR-5 通关后的项目语境讨论(2026-05-08)。
- **Date**: 2026-05-08
- **Fact**: 东京 3 试験场(府中 / 鮫洲 / 江東)实测可订日期常排到
  1+ 月以后,而其他都道府県(北海道、東北、新潟等)同类笔试可订
  日期通常在数日内。这是**供需结构性失衡**,不是个别用户行为问题。
- **解读**(项目战略层):
  - **v1 个人代下单**只是把"先到先得"的赢家从手快者切到脚本拥有者,
    总 slot 数零和不变。零和工具对单个用户(本项目作者)是赢,
    对全体使用人群是 prisoner's dilemma。
  - **v2 真正高价值定位**应是**跨都道府県路由**:用户输入"我能去
    的范围 + 时间预算",系统自动查 13 都道府県候选 slot,推送最早
    可订的。这把"零和 race"转化为"扩大供给检索",对结构性问题
    才真的有缓解作用。
  - 这条战略**不是 v1 范围**,落档作为 v2 的方向指南,避免 v2 滑
    向"更快的东京抢手脚本"。
- **Anchor**: 用户视角的产品方向陈述(2026-05-08);供给侧实测
  数据待 v1 上线后第一周实地采集 13 都道府県对照表。

### WAF 拦截非浏览器 UA(2026-09-06)

- **Task context**: `probe_calgetres.py` 突然全 403,排查请求头。
- **Date**: 2026-09-06
- **实测**(`GET /calgetres?date=202609&coursecode=12&placecode=270&user=pub`):
  - curl 默认 UA + Referer → 403
  - Chrome UA,不带 Origin/Referer → 200
  - Chrome UA + `Origin: chrome-extension://…` → 200(WAF 只看 UA)
- 响应 `access-control-allow-origin` 写死 `https://license-test.tokyo-madoguchi-yoyaku.com`。
  浏览器扩展跨域读需 MV3 background + `host_permissions`,或从页面 content
  script 同源发请求。
- 2026-05 时 httpx 默认 UA 可通,故 5 月记录的 `/putres` "标准 User-Agent"
  现已不成立;`codes.USER_AGENT` 统一供 calgetres 与 putres 使用。
- **Anchor**: 本机 curl(2026-09-06 17:4x JST)。
