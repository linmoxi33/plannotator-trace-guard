# plannotator-trace-guard

**Check a Quint ITF trace of Plannotator's own update-lifecycle model against the 14 properties that model declares — offline, in one command, with no `quint` and no Apalache installed.**

Plannotator ships a formal state machine for how an install learns of a release, updates, restarts and shows "What's new" (`spec/update-lifecycle.qnt`, 964 lines, 6 instances, 14 properties). Its own spec README says the properties are **never verified** and that even random simulation needs the Quint backend downloaded from GitHub:

> `Nothing here runs `quint verify`. That needs Apalache, which this work did not download.`
> `On first use, Quint's rust backend downloads its evaluator from Quint's GitHub releases into ~/.quint`
> — [spec/README.md](https://github.com/backnotprop/plannotator/blob/main/spec/README.md)

So a trace produced by `quint run … --out-itf` is today only *printed* by `spec/show-trace.ts`; nothing checks it. This tool reads that same ITF JSON and evaluates the spec's properties over every state, reporting the step, the action and the exact numbers behind each violation.

Upstream: https://github.com/backnotprop/plannotator

- Upstream property list (line-numbered): [`spec/update-lifecycle.qnt`](https://github.com/backnotprop/plannotator/blob/main/spec/update-lifecycle.qnt#L605-L699)
- Upstream trace printer (printing only, no judgement): [`spec/show-trace.ts`](https://github.com/backnotprop/plannotator/blob/main/spec/show-trace.ts)
- Upstream says the model was written against `main` at `35f9ad90d`.

## 本轮创新

**B 档.** 第一次把 Plannotator 自己写了却没人执行的 14 条 Quint 不变式变成**离线、可退出码判定**的检查器：上游文档明确说没人跑过 `quint verify`，而随机模拟又必须先联网下载 Quint 后端；本工具用纯 stdlib 复刻 ITF 解码 + 全部 14 条 `val` 的判定，让任何一条 `quint run` 产出的 trace 无需安装 Quint 即可得 PASS/FAIL 与逐步 witness。与「同类离线审计器」的区别是：判定对象不是文件格式，而是**上游形式化模型自己声明的安全/有界性质**（含 `LOCK_LIVE_MAX`、`WHATS_NEW_BOUND`、`OFFER_BOUND` 等 12 个上游常量），并且每条实现都带上游行号、可用 `tools/check_fidelity.py` 对本地 spec 逐条核对。

## 它做什么 / 不做什么

**做**：解析 Quint ITF trace（`#bigint` / `#map` / `#set` / `#tup`），解码 `s` 或 `*::s` 状态变量，对每个 state 判定 14 条性质，输出人读报告或 `--json`；`--windows` / `--notes-fix` / `--whats-new-fix` 切换 spec 的 instance 参数。

**不做**：不跑 `quint verify`，不做符号模型检查，不生成 trace，不猜上游语义。它只回答「这条 trace 是否违反了 spec 里已经写下的这些性质」。如果 trace 缺字段，该性质会被标成 `skipped` 并写出缺了哪些字段，不会静默通过。

## 安装 / 用法

无第三方依赖，Python ≥ 3.9。

```sh
git clone git@github.com:linmoxi33/plannotator-trace-guard.git
cd plannotator-trace-guard
python3 plannotator_trace_guard.py --list          # 14 properties + upstream line refs
python3 plannotator_trace_guard.py examples/in/clean.itf.json
```

在 Plannotator 仓库里产出一条真实 trace：

```sh
quint run spec/update-lifecycle.qnt --out-itf /tmp/trace.itf.json --max-samples 2000
python3 /path/to/plannotator-trace-guard/plannotator_trace_guard.py --json /tmp/trace.itf.json
```

### 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 每条性质在它检查过的每个 state 上都成立 |
| `1` | 至少一条性质被违反（stdout 给出 step / act / now / 具体数值） |
| `2` | 用法或输入错误（不是 ITF、没有 `s` 变量、`--only` 写了未知性质、`--ids` 非法） |

### `--json` 输出示例（真实运行，截断）

```json
{
  "tool": "plannotator-trace-guard",
  "version": "0.1.0",
  "trace": "examples/in/whats-new-link-missing.itf.json",
  "state_var": "s",
  "ids": [1, 2, 3],
  "flags": {"windows": false, "notes_fix": false, "whats_new_fix": false, "draft_flow": false},
  "states": 3,
  "violations_total": 3,
  "verdict": "fail",
  "exit_code": 1
}
```

完整输出见 `examples/out/*.json`（由 `python3 tools/make_examples.py` 真实运行生成）。

## 例子与预期结果

| 输入 | 结果 | 退出码 |
| --- | --- | --- |
| `examples/in/clean.itf.json` | 全部 14 条通过 | `0` |
| `examples/in/whats-new-link-missing.itf.json` | `WhatsNewWithinRun` / `WhatsNewAfterBlip` / `WhatsNewAfterAbandonedRequest` 各 1 条 witness（`WhatsNew owed for v=2 but link=0`） | `1` |
| `examples/in/lock-overlap.itf.json` | `NoTwoInstallers`：两个 installer 同时运行 100m < `LOCK_LIVE_MAX`=1440m | `1` |
| 同一条 + `--windows` | 通过：Windows 的界限是 `LOCK_STALE`=60m | `0` |

最后一行就是 `--windows` 的意义：POSIX 允许「第一个 installer 跑过一天」，Windows 只允许「跑过一小时」；同一份 trace 在两种 instance 下结论相反。

## 测试

```sh
python3 tests/run.py
```

当前 **25 passed, 0 failed**，其中 8 条是行为用例（subprocess 退出码 0/1/2、`--json` 可被 `json.load` 解析、`--only` 过滤、`--windows` 翻转 verdict、`--list` 条数、`examples/out` 与全新运行逐字段一致）。

## Fidelity（防止「上游没这么写，我们却这么断言」）

每条性质在代码里都标注了它来自 `spec/update-lifecycle.qnt` 的哪几行；12 个常量也逐个注明了行号。把上游 spec 存到本地后：

```sh
python3 tools/check_fidelity.py /path/to/update-lifecycle.qnt
```

它做两层核对：(1) 13 个常量数值 + 14 个 `val` 名字必须与 spec 一致；(2) **逐性质比对常量引用**——把 spec 里每条 `val` 正文提到的常量集合，与该性质的实现正文提到的常量集合做集合相等判定（`NoTwoInstallers` 只许出现 `LOCK_STALE`/`LOCK_LIVE_MAX` 这类）。任何不一致以退出码 `1` 报出；把 spec 里某个常量改掉即可看到它报 `MISMATCH constant …`。实测：`ok: 13 constants, 14 property names, 14 property bodies agree`。

## 与同类项目的差异

搜过（命令与命中见仓库外的阶段 D 记录）：`gh search repos "quint itf trace"`、`gh search repos "ITF trace validator"` 均 0 命中；
`gh search code "out-itf"` 命中 21 处，全部是**产出** trace 的代码（含上游 `spec/simulate.sh`、`spec/show-trace.ts`）。
elizaOS 有 `packages/*/quint/check-*.mjs` 这类逐性质手写脚本，但那是它自己内部 harness 的专用检查、
每加一条性质就要新写一个脚本，不能读任意 ITF trace；本工具只依赖 spec 里已声明的 `val` 名字与常量，
换 spec 后重跑 `tools/check_fidelity.py` 即可，不复制任何上游代码。

## 局限

- 只实现 spec **已经写下**的 14 条性质；`DRAFT_FLOW` 只作为 instance 开关透传，不影响这 14 条。
- 本工具不判断这些性质是否「等价于」TypeScript 代码的行为——那是上游 `fast-check` twin（`packages/server/update-lifecycle.model.test.ts`）的工作；本工具只判定 trace 是否违反 spec。
- 缺字段的 state 会让相关性质标为 `skipped`（并列出字段名），这是有意的：不会把「读不到」当成「通过」。
- 上游改 spec 后需要同步本工具；`tools/check_fidelity.py` 就是为此准备的。
- 手写样例（`examples/in/`）是最小 ITF，不是从真实运行导出的 trace；真实 trace 请用上面的 `quint run` 命令自己产出。

## License

MIT。上游 Plannotator 为 Apache-2.0 / MIT 双许可；本仓库不含上游代码，只调用其公开的 ITF 格式与 spec 常量。
