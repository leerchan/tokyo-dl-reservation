# DL Reservation — Architecture

**Last updated**: 2026-05-08

## Overview

自动化"补位预约"工具:针对日本驾照笔试(初版面向东京 3 個試験場)
的官方在线预约系统,定时轮询是否有他人取消后释放的空缺日程,在
用户指定的最晚可接受日期之前出现空格时通知用户(并在未来版本中
代为下单)。当前阶段只有 compass 文档骨架,无代码。

## Module layout

(还未开始实现 — 留待 v0 原型确定抓取层选型后再写)

预期 v0 顶层模块:

- `scraper/` — 各試験場预约页面的抓取 + 解析。每个試験場一个适配器,
  共享一个统一的 `AvailabilitySnapshot` schema(见下)。
- `poller/` — 调度层。每日定时调用 scraper,产生当前空缺集合并 diff
  上一次快照,识别"新出现"的空缺。
- `matcher/` — 把单个用户的预约请求(license type, prefecture,
  preferred 試験場, latest_acceptable_date)与新空缺匹配。
- `notifier.py` — 命中时通知用户:stdout 常开,邮件(SMTP)/ Bark(iOS 推送)按环境变量启用,`TeeNotifier` 扇出。
- `config/` & `state/` — 用户请求模型 + 上次快照持久化(v0 用本地
  文件 / SQLite 即可)。

v2(产品化)新增:

- `web/` — 用户提交请求 + 付款 + 查看状态的前端 + API。
- `worker/` — 把每个用户请求实例化为一条带超时(默认 1 周)的轮询任务。
- `metrics/` — 成功率、平均补位时间等运营指标。

## Data flow

v0(单用户、本地):
1. 用户在配置文件里写入 1 个 `ReservationRequest`
   (license type, prefecture=tokyo, candidate 試験場 列表,
   `latest_acceptable_date`, 通知渠道)。
2. cron / launchd 每 N 分钟触发 `poller`。
3. `poller` 对每个候选試験場调用对应的 `scraper`,得到当前
   `AvailabilitySnapshot`。
4. 与上一次快照 diff,新增的空缺日期若 ≤ `latest_acceptable_date`
   且匹配请求条件,交给 `notifier` 推送。
5. 写回最新快照覆盖旧快照。

v2(多用户、产品化):
1. 用户通过 web 提交请求 + 付款 → 入库一条 `ReservationJob`,默认
   TTL 1 周。
2. `worker` 在 TTL 内按定时轮询每个 job;命中后通知/(可选)代下单
   并 close job。
3. job 关闭后写入运营指标。

## Our schemas

**ReservationRequest** (v0):
- `license_type`: enum (e.g. 普通, mynumber+license 合并, 等待 §STATUS
  P2 调研明确具体取值)
- `prefecture`: enum (v0 仅 `tokyo`)
- `candidate_centers`: list of 試験場 标识符
- `latest_acceptable_date`: date
- `notify_channels`: list (email/line/...)

**AvailabilitySnapshot** (v0):
- `center`: 試験場 标识符
- `fetched_at`: timestamp
- `slots`: list of `(date, slot_meta)` — slot_meta 形态待 §STATUS P1 抓取
  调研后定义(可能含 session 时段、剩余席位等)。

**ReservationJob** (v2 预留,v0 不实现):
- 包含 `ReservationRequest` + `user_id` + `created_at` + `expires_at`
  (默认 created_at + 7d) + `status` + `charged_amount`。
