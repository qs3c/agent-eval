# 全局模型池

左侧 **模型设置** 与实验记录、创建实验、RSI 改进同级。添加模型时填写显示名称、模型 ID、Chat Completions 服务地址、API Key 和 Token 限制。DeepSeek 官方服务会自动补齐 Messages 地址；其他兼容服务若要给 DSH 使用，需提供 Messages 服务地址。接口兼容性依赖提供商，保存不发起联网测试或模型调用。

下方分别指定执行 BKCrab、执行 DSH、实验 Agent、守护 Agent 的默认模型。RSI 执行器卡片只选择池内模型和时限；创建实验页可覆盖本次实验与守护模型，不修改场景默认值。真实评测中参与 Agent 共用实验模型，以保留比较条件；离线/mock 模板不能因此变成真实模型实验。守护模型只在模板已启用 assist 时调用，observe 保持观察模式。实验/守护真实调用继续遵守服务 `--allow-live` 和提交费用确认。

首次启动把旧 RSI 模型与现有 DeepSeek 凭据迁入池中，默认分配至各场景；不改变正在运行或已创建的记录。模型池元数据在 `.console/models.json`，密钥在 `.console/model-keys/`，文件权限为 `0600`。API 不回传密钥，编辑时留空保留。更新密钥生成新引用；旧引用留存供已冻结任务使用。尚被场景默认引用的条目不能直接移除，需要先重新分配；移除条目不删除历史凭据快照。

实验配置快照保存 model 的 name、api_base、messages_base、max_tokens、context_window、pool_id 和 api_key_env 引用。工作进程只注入该任务使用的密钥。RSI 原生执行命令同样冻结模型参数、镜像与凭据引用。模型池不改写自定义 RSI profiles。

`GET/POST /api/models`、`PUT/DELETE /api/models/{id}` 管理模型，`PUT /api/model-bindings` 设置场景。拒绝未知字段、带认证信息/查询参数的接口地址和无效 Token 范围；验证错误不包含提交的密钥。所有写入沿用同源限制。

内置守护驱动使用选定的 Chat Completions 接口，通过既有 JSONL snapshot / exec / finish 协议行动；守护器仍独立检查权限、时限、动作数及实际进展。此配置功能不代表已经验证模型诊断或改进效果。

验证：`pytest tests/test_model_pool.py tests/test_rsi_executors.py tests/test_console.py tests/test_guardian.py tests/test_live.py -q`；前端沿用 `NODE_PATH=.cache/rsi-ui/node_modules node --test tests/rsi_ui.test.cjs`。真实模型性能和供应商兼容性需要另行运行实验验证。
