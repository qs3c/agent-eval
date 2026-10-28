"""Instructions for external tools; reading these never launches an agent."""

from pathlib import Path

from .sources import Sources, repository


def instructions(root, url, ref="main", source_id=None):
    repo = repository(url)
    # Use the same ref rules as source registration without registering a job.
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref) or ".." in ref or "//" in ref or ref.endswith(("/", ".lock")):
        raise ValueError("Invalid source ref")
    root = Path(root).resolve()
    observed = None
    if source_id:
        source = Sources(root).get(source_id)
        if source["repository"] != repo or source["ref"] != ref:
            raise ValueError("源码信息与来源记录不一致")
        observed = source["observed"]
    text = f"""请将以下目标 Agent 接入 Agent Harness Eval。
平台源码目录（平台主机路径）：{root}
目标仓库：{repo}
目标 ref：{ref}
已观察 SHA：{observed or '尚未观察；请解析并记录完整 commit SHA'}
来源 ID：{source_id or '尚未登记；完成后在平台页面提交配方'}

1. 阅读平台 README.md、docs/agent-integration.md，以及目标 Agent 源码。
   字段定义见 src/agent_harness_eval/integrations/schema.py，协议实现见 agents/recipe.py。
2. 保持目标 checkout 干净，在独立目录生成 recipe.yaml 和必要 patch；保留原生循环、工具与上下文管理。
   固定实际源码 SHA、镜像 digest 和依赖；从源码构建，不能用发布包冒充该 SHA。
3. 实现标准 JSON 请求/响应、模型中继及原生轨迹。模型 Agent 支持 mock/live，禁止硬编码控制题答案。
   不包含密钥、验证器或隐藏答案，不修改平台评分逻辑。记录能力、功能差异和覆盖局限。
4. 使用 integration validate 检查配方；在具备 Linux/Docker 的平台主机通过 integration build 构建，
   或回到本页面提交配方绝对路径，交由平台统一构建和执行两题控制验收。
5. 交付配方和全部相对路径引用的 patch、完整源码 SHA、实际执行命令与证据。
   生成成功不是接入成功。平台检查 Release 来源、至少两次完整隔离尝试和标准响应；
   题目得分由独立验证器判定。Comparable 当前未认证，不自行声明通过。

如果你的开发工具在另一台机器，请自行获取平台和目标仓库源码，并将交付物复制到平台主机后提交。
无需安装平台接入执行器或调用 source assist。未执行的构建/验证必须明确注明。
"""
    return {"instructions": text, "repository": repo, "ref": ref, "source_id": source_id, "observed": observed}
