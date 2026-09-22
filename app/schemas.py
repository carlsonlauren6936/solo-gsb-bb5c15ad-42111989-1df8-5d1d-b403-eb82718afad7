"""HTTP API 的 Pydantic 模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ConflictPolicy = Literal["collect_all", "priority", "first_hit"]


class RuleIn(BaseModel):
    code: str = Field(..., min_length=1, description="规则编码")
    name: str | None = None
    description: str | None = None
    priority: int = 0
    conflict_group: str | None = None
    conflict_policy: ConflictPolicy | None = None
    depends_on: list[str] = Field(default_factory=list)
    condition: str = Field("", description="条件表达式；空串视为恒真")
    action: str | None = Field(None, description="动作表达式，命中时求值")


class RulesetCreate(BaseModel):
    id: str | None = Field(None, description="可选自定义 ID")
    name: str = Field(..., min_length=1)
    description: str = ""
    default_conflict_policy: ConflictPolicy = "collect_all"
    rules: list[RuleIn] = Field(..., min_length=1)


class RulesetUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    default_conflict_policy: ConflictPolicy = "collect_all"
    rules: list[RuleIn] = Field(..., min_length=1)


class RulesetMeta(BaseModel):
    id: str
    name: str
    description: str
    default_conflict_policy: ConflictPolicy
    current_version: int
    created_at: str
    updated_at: str


class RuleOut(BaseModel):
    code: str
    name: str
    description: str = ""
    priority: int = 0
    conflict_group: str | None = None
    conflict_policy: ConflictPolicy | None = None
    depends_on: list[str] = []
    condition: str = ""
    action: str | None = None
    referenced_rules: list[str] = []


class RulesetVersionOut(BaseModel):
    id: str
    ruleset_id: str
    version: int
    name: str
    description: str
    default_conflict_policy: ConflictPolicy
    rules: list[RuleOut]
    evaluation_order: list[str]
    created_at: str


class EvaluateRequest(BaseModel):
    document: Any = Field(default_factory=dict)


class EvaluateBatchRequest(BaseModel):
    documents: list[Any] = Field(
        ...,
        max_length=100,
        description="待评估文档数组，单次最多 100 项",
    )
    version: int | None = Field(
        None, ge=1, description="整批固定使用的规则版本；缺省为最新版"
    )


class BatchEvaluationItem(BaseModel):
    index: int = Field(..., description="结果对应输入 documents 中的下标")
    status: Literal["success", "error"]
    result: EvaluationOut | None = Field(
        None, description="求值成功时的完整决策结果"
    )
    error: ErrorResponse | None = Field(
        None, description="该项求值失败的错误信息，不影响其他项"
    )


class BatchEvaluationOut(BaseModel):
    ruleset_id: str
    version: int = Field(..., description="整批实际使用的规则版本")
    total: int
    success_count: int
    error_count: int
    results: list[BatchEvaluationItem]


class ConflictExplanation(BaseModel):
    group: str | None = None
    policy: str
    winner: str | None = None
    suppressed: list[dict[str, Any]] = []
    field: str | None = None
    previous_winner: str | None = None
    reason: str | None = None


class RuleTrace(BaseModel):
    rule_code: str
    hit: bool
    value: Any = None
    error: str | None = None


class EvaluationOut(BaseModel):
    id: str
    ruleset_id: str
    version: int
    document: Any
    fired_rule_codes: list[str]
    hit_rule_codes: list[str]
    evaluation_order: list[str]
    iterations: int
    converged: bool
    decision: dict[str, Any]
    conflicts: list[dict[str, Any]]
    traces: list[dict[str, Any]]
    created_at: str


class RecomputeRequest(BaseModel):
    document: Any | None = Field(
        None, description="为空则使用该规则集上一次决策的文档"
    )


class RuleStatOut(BaseModel):
    code: str = Field(..., description="规则编码")
    hit_count: int = Field(..., description="traces 中 hit=true 的决策条数")
    hit_rate: float = Field(
        ..., description="hit_count / total_evaluations，保留 4 位小数"
    )


class DecisionFieldCountOut(BaseModel):
    field: str = Field(..., description="决策结果的顶层字段名")
    count: int = Field(..., description="该字段在多少条决策结果中出现")


class EvaluationStatsOut(BaseModel):
    ruleset_id: str
    version: int | None = Field(None, description="回显 version 筛选条件")
    since: str | None = Field(
        None, description="回显归一化（UTC 秒级）后的 since 筛选条件"
    )
    until: str | None = Field(
        None, description="回显归一化（UTC 秒级）后的 until 筛选条件"
    )
    total_evaluations: int = Field(..., description="筛选后命中的持久化决策条数")
    rule_stats: list[RuleStatOut] = Field(
        ..., description="按规则编码排序；无匹配记录时为空数组"
    )
    decision_field_counts: list[DecisionFieldCountOut] = Field(
        ..., description="按字段名排序；无匹配记录时为空数组"
    )


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}
