# /tmp/similar.md — 阶段 D 同类检索（真实执行，2026-11-16）

命令与命中：
1. `gh search repos "quint itf trace" --limit=15 --json fullName,description,stargazersCount` → 0 命中
2. `gh search repos "ITF trace validator" --limit=15 --json fullName,description,stargazersCount` → 0 命中
3. `gh search code "out-itf" --limit=20 --json repository,path` → 21 命中，全部是**产出** trace 的代码，没有消费 trace 做性质判定的工具：
   - backnotprop/plannotator  spec/simulate.sh
   - backnotprop/plannotator  spec/show-trace.ts   （只 console.log 打印）
   - elizaOS/eliza  packages/*/quint/check-*.mjs  （eliza 自己的逐性质手写脚本，与其内部 harness 绑定，不是通用 ITF 性质检查器）
4. 判断：**没有 mature 同类**（stars>500 且功能覆盖）。elizaOS 的 `quint/check-*.mjs` 是每个 spec 手写一次的脚本，
   和「读 ITF trace × 判定 spec 自己声明的 val」不是同一件事；本仓库不依赖它、也不复制它。
   因此 README 不设「与同类项目的差异」强制章节，仅说明与 elizaOS 手写脚本的区别。
