# Trace 采集与证据口径

本说明对应 agent-harness-eval 的实际实现。Trace 是 Agent 原生事件与模型代理观测的组合，不是完整读取模型内部决策原因，也不是通过一份最终回答反推执行过程。

## 一次请求如何被记录

Harbor 为每个任务/Agent/尝试保留独立 `jobs/<agent>/<trial>/agent/` 目录。Agent 结束后 `Native.populate_context_post_run` 转换轨迹与用量，控制台只读这些文件。`result.json` 中的 Agent 执行时间包含运行器的边界，与代理单次模型请求窗口不同。

## 两边的原生埋点

## v2 新增采集

- 每个 Agent 发给中转的 HTTP 请求有唯一 `request_id`；原始、有效请求、流事件、阶段时间、结束记录都可以据此关联。中转重试用 `(request_id, attempt)` 区分上游尝试。旧 `wire_requests.jsonl` 格式保留以兼容 ATIF 转换，新 `effective_wire_requests.jsonl` 增加 ID 包装。
- `wire_timing.jsonl` 每条含 schema 版本、UTC 时间、协议和从请求处理开始计算的 `elapsed_ms`；时间差使用本进程 `monotonic_ns`，不跨进程相减。
- 阶段为 `received`、`upstream_start`、`headers`、`first_event`、`first_output_delta`、`upstream_end`、`finished`。首事件可能只是协议元数据；首输出增量包括文本、推理或工具参数，不标成用户可见 TTFT。
- 窗口包含网络、上游排队/生成、代理日志和客户端背压；`upstream_end` 在读完并转发后记录。不能拆成纯模型计算/纯网络时间，也不能把各请求窗口相加当作任务总时长。
- `wire_responses.jsonl` 记录 `completed`、`truncated`、`http_error`、`provider_error` 或 `transport_or_client_error`，以及 `stream_complete`。Chat 流需 `[DONE]`，Messages 流需 `message_stop`；HTTP 200 本身不足以证明完成。
- 保留缓存命中与未命中输入；Messages 的 `input_tokens` 不含缓存，需要加上 cache read/create。流式 usage 更新合并，不对缺失输出量填零。授权请求头不写日志；页面不展示 reasoning 内容。

## 关联与完整性

控制台从供应商响应中取 tool ID，再与原生工具事件的 ID 配对；不依赖两个文件第 N 行必然是同一次调用。没有工具 ID 的最终回复仍可能按日志顺序关联，页面标注 `sequence_only`；冲突/缺失则不能称精确关联。

汇总用量同时检查请求/响应 ID 唯一且集合一致、必需计数器存在且非负、v2 流成功结束。失败时总用量未知，单个已观测响应仍可供排查。旧离线 mock 没有 request_id，兼容为 `legacy_count_only`，不伪称精确关联。coverage 中区分代理已捕获请求与 Agent 是否有 Provider hook，修正 DSH 没有 `model_request` 就被误判没采集请求的问题。

`trajectory.json` 的 ATIF 仍是末次请求上下文加终态输出；可能看不到之前压缩掉的消息。完整执行分析应联合原生事件与 wire 文件。历史人工分析在 `console_reviews.json`，不自动作为新运行的结论。

## 验证与尚存边界

### 中转有界重试（2026-10-14）

重试只包围取得上游响应头的操作；收到响应后直接转发，读取或发送响应体失败均不重放。三次尝试及退避共享原有响应头等待预算（主请求 300 秒，搜索 60 秒），不会改动监督进程的原题硬期限。它不是整个响应流的总时限，也不保证上游未处理或未计费；不能用客户端未收到响应推断上游从未执行。

`wire_attempts.jsonl` 保存每次打开连接/等待响应头的 UTC 起始时间、耗时、状态码、异常类型、底层原因类型、退避秒数和 `retry`/`failed`/`headers_received` 结果。原始异常文本和认证信息不落盘；`headers_received` 不能解释为完整生成成功，最终流结果仍看 `wire_responses.jsonl`。重试失败尝试的供应商用量未知，最终成功响应的 usage 不能代表所有尝试的实际账单。

本次改动只影响使用新版中转创建的运行环境，已启动容器内的脚本和冻结历史证据不会被替换。

本次使用本机 HTTP 测试服务器和模拟上游验证两种协议的流转发、阶段顺序、ID、流截断和用量完整性；没有新增付费模型调用。新计时需要下一次真实运行才能得到实际数值。