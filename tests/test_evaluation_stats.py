"""GET /api/rulesets/{id}/evaluation-stats 接口测试。"""

import sqlite3
from datetime import datetime, timezone

from app.database import connect


def _set_eval_timestamp(evaluation_id: str, created_at: str) -> None:
    """直接改写落库决策的 created_at，使 since/until 筛选可确定性测试。

    写入值统一归一化为应用实际的存储格式：UTC 秒级 naive ISO 字符串。
    """
    dt = datetime.fromisoformat(created_at)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    normalized = dt.isoformat(timespec="seconds")
    conn: sqlite3.Connection = connect()
    try:
        conn.execute(
            "UPDATE evaluations SET created_at = ? WHERE id = ?",
            (normalized, evaluation_id),
        )
        conn.commit()
    finally:
        conn.close()


def _stats(client, rid, **params):
    resp = client.get(f"/api/rulesets/{rid}/evaluation-stats", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_stats_zero_value_structure_when_no_evaluations(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    body = _stats(client, rid)
    assert body == {
        "ruleset_id": rid,
        "total_evaluations": 0,
        "rule_stats": [],
        "decision_field_counts": {},
    }


def test_stats_aggregates_traces_and_decision_fields(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]

    # 两次命中 r1+r2，一次均不命中
    for doc in ({"x": 42}, {"x": 100}, {"x": 1}):
        resp = client.post(f"/api/rulesets/{rid}/evaluate", json={"document": doc})
        assert resp.status_code == 200, resp.text

    body = _stats(client, rid)
    assert body["ruleset_id"] == rid
    assert body["total_evaluations"] == 3
    # 按 code 稳定排序
    assert [s["code"] for s in body["rule_stats"]] == ["r1", "r2"]
    r1 = next(s for s in body["rule_stats"] if s["code"] == "r1")
    r2 = next(s for s in body["rule_stats"] if s["code"] == "r2")
    assert r1["hit_count"] == 2
    assert r2["hit_count"] == 2
    assert r1["hit_rate"] == 2 / 3
    assert r2["hit_rate"] == 2 / 3
    # decision 字段在两次决策中出现
    assert body["decision_field_counts"] == {"bonus": 2, "level": 2}


def test_stats_rule_with_zero_hits_still_listed(client, demo_id):
    # 全部文档都不命中任何规则时，traces 中每个规则 hit=false 仍应统计
    client.post(
        f"/api/rulesets/{demo_id}/evaluate",
        json={"document": {"amount": 0}},
    )
    body = _stats(client, demo_id)
    assert body["total_evaluations"] == 1
    codes = [s["code"] for s in body["rule_stats"]]
    assert codes == sorted(codes)
    assert "manual_review" in codes
    assert all(s["hit_count"] == 0 for s in body["rule_stats"])
    assert all(s["hit_rate"] == 0.0 for s in body["rule_stats"])
    assert body["decision_field_counts"] == {}


def test_stats_hit_rules_are_sorted_by_code(client):
    payload = {
        "name": "unordered",
        "rules": [
            {"code": "zebra", "condition": "true", "action": '{"z": 1}'},
            {"code": "alpha", "condition": "true", "action": '{"a": 1}'},
            {"code": "mid", "condition": "true", "action": '{"m": 1}'},
        ],
    }
    rid = client.post("/api/rulesets", json=payload).json()["ruleset_id"]
    client.post(f"/api/rulesets/{rid}/evaluate", json={"document": {}})
    body = _stats(client, rid)
    assert [s["code"] for s in body["rule_stats"]] == ["alpha", "mid", "zebra"]
    assert list(body["decision_field_counts"]) == ["a", "m", "z"]


def test_stats_version_filter(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    client.post(f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 100}})

    client.put(
        f"/api/rulesets/{rid}",
        json={
            "name": "simple",
            "rules": [
                {"code": "r1", "condition": 'doc["x"] >= 1000',
                 "action": '{"level": "high"}'}
            ],
        },
    )
    client.post(f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 100}})
    client.post(f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 5000}})

    all_stats = _stats(client, rid)
    assert all_stats["total_evaluations"] == 3

    v1 = _stats(client, rid, version=1)
    assert v1["total_evaluations"] == 1
    assert v1["rule_stats"] == [
        {"code": "r1", "hit_count": 1, "hit_rate": 1.0},
        {"code": "r2", "hit_count": 1, "hit_rate": 1.0},
    ]
    assert v1["decision_field_counts"] == {"bonus": 1, "level": 1}

    v2 = _stats(client, rid, version=2)
    assert v2["total_evaluations"] == 2
    assert v2["rule_stats"] == [
        {"code": "r1", "hit_count": 1, "hit_rate": 0.5},
    ]
    assert v2["decision_field_counts"] == {"level": 1}

    # 存在的规则集 + 不存在（尚无决策）的版本 → 零值结构而非 404
    v9 = _stats(client, rid, version=99)
    assert v9["total_evaluations"] == 0
    assert v9["rule_stats"] == []
    assert v9["decision_field_counts"] == {}


def test_stats_since_until_inclusive_bounds(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    e1 = client.post(
        f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 100}}
    ).json()
    e2 = client.post(
        f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 42}}
    ).json()
    e3 = client.post(
        f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 1}}
    ).json()
    _set_eval_timestamp(e1["id"], "2026-09-01T00:00:00+00:00")
    _set_eval_timestamp(e2["id"], "2026-09-10T12:30:00+00:00")
    _set_eval_timestamp(e3["id"], "2026-09-20T23:59:59+00:00")

    # since 含边界
    body = _stats(client, rid, since="2026-09-10T12:30:00Z")
    assert body["total_evaluations"] == 2

    # until 含边界
    body = _stats(client, rid, until="2026-09-10T12:30:00Z")
    assert body["total_evaluations"] == 2

    # 双侧含边界，恰好框住中间一条
    body = _stats(
        client,
        rid,
        since="2026-09-10T12:30:00+00:00",
        until="2026-09-10T12:30:00+00:00",
    )
    assert body["total_evaluations"] == 1

    # 无交集 → 零值结构
    body = _stats(client, rid, since="2027-01-01T00:00:00Z")
    assert body["total_evaluations"] == 0
    assert body["rule_stats"] == []
    assert body["decision_field_counts"] == {}


def test_stats_since_until_timezone_aware(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    e = client.post(
        f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 100}}
    ).json()
    # UTC 08:00 == 北京 16:00
    _set_eval_timestamp(e["id"], "2026-09-01T08:00:00+00:00")

    body = _stats(client, rid, since="2026-09-01T16:00:00+08:00")
    assert body["total_evaluations"] == 1
    body = _stats(client, rid, until="2026-09-01T16:00:00+08:00")
    assert body["total_evaluations"] == 1
    body = _stats(client, rid, since="2026-09-01T16:00:01+08:00")
    assert body["total_evaluations"] == 0


def test_stats_excludes_unpersisted_evaluations(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    # persist=false 的决策不计入统计
    client.post(
        f"/api/rulesets/{rid}/evaluate?persist=false",
        json={"document": {"x": 100}},
    )
    body = _stats(client, rid)
    assert body["total_evaluations"] == 0

    client.post(f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 100}})
    body = _stats(client, rid)
    assert body["total_evaluations"] == 1
    assert body["rule_stats"][0]["hit_count"] == 1


def test_stats_combined_version_and_time_filters(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    old = client.post(
        f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 100}}
    ).json()
    client.put(
        f"/api/rulesets/{rid}",
        json={"name": "simple", "rules": [{"code": "r1", "condition": "true"}]},
    )
    new = client.post(
        f"/api/rulesets/{rid}/evaluate", json={"document": {"x": 1}}
    ).json()
    _set_eval_timestamp(old["id"], "2026-01-01T00:00:00+00:00")
    _set_eval_timestamp(new["id"], "2026-06-01T00:00:00+00:00")

    # version=1 + 较晚的 since：版本匹配但时间不匹配
    body = _stats(client, rid, version=1, since="2026-03-01T00:00:00Z")
    assert body["total_evaluations"] == 0

    body = _stats(client, rid, version=2, since="2026-03-01T00:00:00Z")
    assert body["total_evaluations"] == 1
    assert body["rule_stats"] == [
        {"code": "r1", "hit_count": 1, "hit_rate": 1.0}
    ]


def test_stats_ruleset_not_found(client):
    resp = client.get("/api/rulesets/nope/evaluation-stats")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_stats_invalid_time_params_return_422(client, simple_ruleset_payload):
    rid = client.post("/api/rulesets", json=simple_ruleset_payload).json()["ruleset_id"]
    resp = client.get(
        f"/api/rulesets/{rid}/evaluation-stats", params={"since": "not-a-date"}
    )
    assert resp.status_code == 422

    resp = client.get(
        f"/api/rulesets/{rid}/evaluation-stats",
        params={"until": "2026-13-99T99:99:99"},
    )
    assert resp.status_code == 422

    # 非法 version 同样是请求级 422
    resp = client.get(
        f"/api/rulesets/{rid}/evaluation-stats", params={"version": "abc"}
    )
    assert resp.status_code == 422
