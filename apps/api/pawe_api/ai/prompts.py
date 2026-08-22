import hashlib

PROMPT_SCHEMA_VERSION = "ai-prompts-1"
POLICY_VERSION = "ai-policy-shadow-1"

PROMPTS: dict[str, str] = {
    "weekly_selection": (
        "分析服务端提供的候选白名单；只能返回白名单代码、证据ID和-10到10调分。"
        "不得新增、删除或发布标的。"
    ),
    "weekly_review": ("基于服务端提供的确定性周终指标，生成结构化摘要和异常；不得改写数字。"),
    "error_attribution": (
        "基于服务端确定性事实和固定错误分类，提出可人工确认的假设；不得声称因果已证明。"
    ),
    "rule_evolution": (
        "仅基于已确认归因提出受限规则 DSL 草案；只能生成 proposed，不得验证、回放、审批或激活。"
    ),
}


def prompt_for(capability: str) -> tuple[str, str]:
    prompt = PROMPTS[capability]
    return prompt, hashlib.sha256(prompt.encode()).hexdigest()
