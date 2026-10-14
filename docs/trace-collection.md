# Trace 采集与证据口径

本说明对应 agent-harness-eval 的实际实现。Trace 是 Agent 原生事件与模型代理观测的组合，不是完整读取模型内部决策原因，也不是通过一份最终回答反推执行过程。

## 一次请求如何被记录

```text
Harbor 任务容器
  ├─ bkcrab-eval：Go Provider 包装器 + SessionEventSink
  └─ DSH Standard：session/event 订阅
            │ 原生工具/步骤/终态 → events.jsonl
            ↓ HTTP 请求
  本地模型代理 127.0.0.1:18765
    原始请求 → native_wire_requests.jsonl
    统一采样参数 → wire_requests.jsonl + effective_wire_requests.jsonl
    上游响应流 → wire_chunks.jsonl
    阶段时间 → wire_timing.jsonl
    结束状态/用量/工具 ID → wire_responses.jsonl
            ↓
  DeepSeek 官方 API
```

Harbor 为每个任务/Agent/尝试保留独立 `jobs/<agent>/<trial>/agent/` 目录。Agent 结束后 `Native.populate_context_post_run` 转换轨迹与用量，控制台只读这些文件。`result.json` 中的 Agent 执行时间包含运行器的边界，与代理单次模型请求窗口不同。

## 两边的原生埋点

| 层面 | bkcrab | DSH Standard |
| --- | --- | --- |
| 安装与启动 | 上传校验过哈希的 `bkcrab-eval` 二进制 | 上传校验过哈希的 DSH bundle，挂载官方 Standard preset |
| 模型事件 | Go `tracedProvider` 包装 `Chat` / `ChatStream`；在序列化前写 `model_request`，保留 `call_id`、流事件、结束/错误 | 本项目 driver 订阅 `session/event`，保存原生 `step/start`、`assistant/message`、`step/end` 等事件 |
| 工具事件 | `ContextWithStream` 注入 evidence sink，经 `AppendSessionEvent` 保存 `tool_call` / `tool_result` | 订阅回调保存 `tool/call` / `tool/result`，含 `turn`、`step`、`callId`、原生时间等 |
| 时间/顺序 | evidence sink 写 UTC 时间和递增 `seq`；工具事件可能按批交付 | driver 写观察时间，内层保留上游事件的 `seq` 和时间；这些是事件边界，不自动等于工具内部开始/结束 |
| 协议 | 本次配置使用 Chat Completions | 本次配置使用 Messages |

bkcrab 的埋点源码位于相邻 bkcrab 仓库 `cmd/bkcrab-eval/main.go`、`trace.go`。本次未修改该仓库，也未修改 DSH 上游 Agent loop。两个 Agent 都经过本项目的同一个代理，所以新增代理埋点对两边使用同一口径。代理源码会在下一次真实评测时上传，且副本保存在证据目录；无需更换 Agent 二进制来启用它。

## v2 新增采集

- 每个 Agent 发给中转的 HTTP 请求有唯一 `request_id`；原始、有效请求、流事件、阶段时间、结束记录都可以据此关联。中转重试用 `(request_id, attempt)` 区分上游尝试。旧 `wire_requests.jsonl` 格式保留以兼容 ATIF 转换，新 `effective_wire_requests.jsonl` 增加 ID 包装。
- `wire_timing.jsonl` 每条含 schema 版本、UTC 时间、协议和从请求处理开始计算的 `elapsed_ms`；时间差使用本进程 `monotonic_ns`，不跨进程相减。
- 阶段为 `received`、`upstream_start`、`headers`、`first_event`、`first_output_delta`、`upstream_end`、`finished`。首事件可能只是协议元数据；首输出增量包括文本、推理或工具参数，不标成用户可见 TTFT。
- 窗口包含网络、上游排队/生成、代理日志和客户端背压；`upstream_end` 在读完并转发后记录。不能拆成纯模型计算/纯网络时间，也不能把各请求窗口相加当作任务总时长。
- `wire_responses.jsonl` 记录 `completed`、`truncated`、`http_error`、`provider_error` 或 `transport_or_client_error`，以及 `stream_complete`。Chat 流需 `[DONE]`，Messages 流需 `message_stop`；HTTP 200 本身不足以证明完成。
- 保留缓存命中与未命中输入；Messages 的 `input_tokens` 不含缓存，需要加上 cache read/create。流式 usage 更新合并，不对缺失输出量填零。授权请求头不写日志；页面不展示 reasoning 内容。

## 关联与完整性

控制台从供应商响应中取 tool ID，再与原生工具事件的 ID 配对；不依赖两个文件第 N 行必然是同一次调用。没有工具 ID 的最终回复仍可能按日志顺序关联，页面标注 `sequence_only`；冲突/缺失则不能称精确关联。

回放现有有效样本：bkcrab 的 22 个工具轮次和 DSH 的 35 个工具轮次可通过 ID 关联；各自最终回复的 1 轮仍按顺序。旧文件未改写，旧请求的新增计时不能事后补出来。

汇总用量同时检查请求/响应 ID 唯一且集合一致、必需计数器存在且非负、v2 流成功结束。失败时总用量未知，单个已观测响应仍可供排查。旧离线 mock 没有 request_id，兼容为 `legacy_count_only`，不伪称精确关联。coverage 中区分代理已捕获请求与 Agent 是否有 Provider hook，修正 DSH 没有 `model_request` 就被误判没采集请求的问题。

`trajectory.json` 的 ATIF 仍是末次请求上下文加终态输出；可能看不到之前压缩掉的消息。完整执行分析应联合原生事件与 wire 文件。历史人工分析在 `console_reviews.json`，不自动作为新运行的结论。

## 验证与尚存边界

### 中转有界重试（2026-10-14）

策略版本为 `pre-response-transient/v1`，写入 `wire_responses.jsonl` 和新增的 `wire_attempts.jsonl`。BKCrab、DSH 的 Chat、Messages 以及辅助搜索共用相同策略：最多三次上游尝试，等待 1、2 秒并分别增加 0–0.25 秒抖动。仅重试连接中断/拒绝、超时、临时 DNS 错误、网络/主机不可达及 HTTP 429/502/503/504；认证、参数、证书错误和未识别错误不重试。

重试只包围取得上游响应头的操作；收到响应后直接转发，读取或发送响应体失败均不重放。三次尝试及退避共享原有响应头等待预算（主请求 300 秒，搜索 60 秒），不会改动监督进程的原题硬期限。它不是整个响应流的总时限，也不保证上游未处理或未计费；不能用客户端未收到响应推断上游从未执行。

`wire_attempts.jsonl` 保存每次打开连接/等待响应头的 UTC 起始时间、耗时、状态码、异常类型、底层原因类型、退避秒数和 `retry`/`failed`/`headers_received` 结果。原始异常文本和认证信息不落盘；`headers_received` 不能解释为完整生成成功，最终流结果仍看 `wire_responses.jsonl`。重试失败尝试的供应商用量未知，最终成功响应的 usage 不能代表所有尝试的实际账单。

本次改动只影响使用新版中转创建的运行环境，已启动容器内的脚本和冻结历史证据不会被替换。

本次使用本机 HTTP 测试服务器和模拟上游验证两种协议的流转发、阶段顺序、ID、流截断和用量完整性；没有新增付费模型调用。新计时需要下一次真实运行才能得到实际数值。

尚未实现工具函数入口/退出处的独立 span、完整父子 Agent span 树、统一跨进程 trace context。尤其 bkcrab 的批次工具返回，现有事件不能证明每个工具真正执行了多少毫秒。要得到该指标，下一步需在原生工具执行器内部记录 start/end、退出码及 parent ID，并重新构建、验证两边的运行包；当前页面不会伪造这部分数据。
