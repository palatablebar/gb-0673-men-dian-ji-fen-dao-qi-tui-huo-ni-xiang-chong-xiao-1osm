# 深度登记 门店积分到期与退货逆向冲销

规模：中型。使用者：连锁零售会员运营专员。核心规则：消费先用最早到期批次；退款按原消费分配逆向恢复，已过期部分不得复活。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 事件采信 | 执行并复核事件采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 权益规则 | 执行并复核权益规则 | `POST /api/policy` | `app/modules/policy/rules.py` | `tests/test_policy.py` |
| M3 积分归批 | 执行并复核积分归批 | `POST /api/lots` | `app/modules/lots/rules.py` | `tests/test_lots.py` |
| M4 消费到期 | 执行并复核消费到期 | `POST /api/consumption` | `app/modules/consumption/rules.py` | `tests/test_consumption.py` |
| M5 退货回拨 | 执行并复核退货回拨 | `POST /api/refund` | `app/modules/refund/rules.py` | `tests/test_refund.py` |
| M6 余额对账 | 执行并复核余额对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 期末归档 | 执行并复核期末归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |
| M8 可用余额发布 | 按会员发布当天可用余额 | `POST /api/runs/{run_id}/publish` | `app/modules/publish/rules.py`、`app/store.py` | `tests/test_publish.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | GB18030权益中文 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_gb18030_real` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-08 | 会员前导零 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_member_leading_zero` |
| TRAP-04 | 重复表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_repeated_header` |
| TRAP-05 | 缺列记录 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_bad` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_duplicate_conflict` |
| TRAP-18 | 空态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_three` |
| TRAP-07 | 哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_sentinel_real` |
| TRAP-11 | UTC归一 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_utc_real` |
| TRAP-06 | 全角积分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_fullwidth_points` |
| TRAP-23 | 权益半开窗 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_rule_end_exclusive` |
| TRAP-31 | 隔离后整批延后 | 无关会员被拖住无法消费 | 按会员粒度发布，仅停用受影响户 | `app/modules/publish/rules.py` | `tests/test_publish.py::test_unaffected_members_stay_usable` |
| TRAP-32 | 异常退款跨户依赖 | 只停用退款发起户，原消费户被误发 | 追踪被引用的原消费会员一并停用 | `app/modules/publish/rules.py` | `tests/test_publish.py::test_dependency_on_other_members_spend` |
| TRAP-33 | 对账失败仍发布 | 错误余额覆盖现场可用版本 | 阻断发布并保留旧版生效 | `app/store.py` | `tests/test_publish.py::test_unbalanced_batch_does_not_replace_active` |
| TRAP-34 | 重跑重复冻结/记账 | 同一批多次发布生成多个版本 | 发布编号由内容指纹决定，重复发布沿用既有版本 | `app/store.py` | `tests/test_publish.py::test_repeat_publish_does_not_add_ledger_entries` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 发布边界

发布只处理当前批次，不展开规则调整或其他批次。原始隔离行、原因码、原始行号随发布结果与归档证据一并保留。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
