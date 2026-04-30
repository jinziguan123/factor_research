"""L2.C: ``llm_eval_diagnose`` 模块测试 + ``_build_eval_feedback`` 路由测试。

不依赖真实 LLM——用 monkeypatch 把 ``factor_assistant._call_openai_compatible``
桩成可控函数。覆盖：
- LLM 返回合规 JSON → 拼成多行文本
- LLM 抛异常 → diagnose_with_llm 也抛
- LLM 返回非法 JSON → ValueError
- _build_eval_feedback：LLM 成功路径 / LLM 失败回落规则版 / 缺 payload 直接走规则版
- _trim_payload：长序列采样保留头尾
"""
from __future__ import annotations

import json

import pytest

from backend.services import eval_service, llm_eval_diagnose
from backend.services.llm_eval_diagnose import (
    _format_llm_response,
    _trim_payload,
    diagnose_with_llm,
)


# ---------------------------- _trim_payload ----------------------------


def test_trim_payload_preserves_short_lists():
    """长度 ≤ 30 的列表原样保留。"""
    payload = {"ic_series": list(range(20))}
    out = _trim_payload(payload)
    assert out["ic_series"] == list(range(20))


def test_trim_payload_samples_long_lists():
    """长度 > 30 的列表保留头 5 + 中间采样 + 尾 5，附 _sampled_from 标记。"""
    payload = {"ic_series": list(range(100))}
    out = _trim_payload(payload)
    val = out["ic_series"]
    assert isinstance(val, dict)
    assert val["_sampled_from"] == 100
    assert len(val["values"]) <= 18  # 5 head + 8 mid + 5 tail


def test_trim_payload_handles_nested_dict():
    """嵌套 dict 里的长 list 也应被裁。"""
    payload = {"per_factor": {"momo": list(range(50)), "rev": [1, 2, 3]}}
    out = _trim_payload(payload)
    assert isinstance(out["per_factor"]["momo"], dict)
    assert out["per_factor"]["rev"] == [1, 2, 3]


# ---------------------------- _format_llm_response ----------------------------


def test_format_llm_response_basic():
    """合规 JSON → 多行文本（summary + actions）。"""
    raw = json.dumps(
        {"summary": "因子表现稳健", "actions": ["进入回测", "搭配 IC 加权"]},
        ensure_ascii=False,
    )
    out = _format_llm_response(raw)
    assert "📋 因子表现稳健" in out
    assert "💡 建议 1：进入回测" in out
    assert "💡 建议 2：搭配 IC 加权" in out


def test_format_llm_response_strips_markdown_fence():
    """LLM 偶尔包 ```json ... ``` 也能解。"""
    raw = "```json\n{\"summary\": \"x\", \"actions\": []}\n```"
    out = _format_llm_response(raw)
    assert "📋 x" in out


def test_format_llm_response_rejects_non_json():
    """非 JSON 文本抛 ValueError。"""
    with pytest.raises(ValueError, match="非合法 JSON"):
        _format_llm_response("这不是 JSON 啊")


def test_format_llm_response_rejects_empty_payload():
    """summary 和 actions 都空 → ValueError（避免落空 feedback）。"""
    with pytest.raises(ValueError, match="均为空"):
        _format_llm_response('{"summary": "", "actions": []}')


# ---------------------------- diagnose_with_llm ----------------------------


def test_diagnose_with_llm_calls_openai_and_formats(monkeypatch):
    """LLM 回正常 JSON → 拼好的文本。"""
    captured: dict = {}

    def _fake_call(messages):
        captured["messages"] = messages
        return json.dumps({
            "summary": "IC 显著，可进入回测",
            "actions": ["在大盘股池验证"],
        }, ensure_ascii=False)

    monkeypatch.setattr(
        "backend.services.factor_assistant._call_openai_compatible", _fake_call,
    )

    out = diagnose_with_llm(
        structured={"ic_mean": 0.06, "ic_ir": 0.8, "long_short_sharpe": 1.5},
        payload={"ic_series": [0.05, 0.06, 0.07]},
        hypothesis="动量延续",
        factor_id="my_factor",
    )
    assert "📋 IC 显著" in out
    assert "💡 建议 1：在大盘股池验证" in out
    # prompt 里应该带上因子身份 + 假设
    user_msg = captured["messages"][-1]["content"]
    assert "my_factor" in user_msg
    assert "动量延续" in user_msg


def test_diagnose_with_llm_propagates_llm_error(monkeypatch):
    """LLM 抛异常 → diagnose_with_llm 不 catch，让上层回落规则版。"""
    def _boom(messages):
        raise RuntimeError("LLM 网络抖了")

    monkeypatch.setattr(
        "backend.services.factor_assistant._call_openai_compatible", _boom,
    )

    with pytest.raises(RuntimeError, match="网络抖了"):
        diagnose_with_llm(
            structured={"ic_mean": 0.05}, payload={}, hypothesis="x", factor_id="f",
        )


# ---------------------------- _build_eval_feedback 路由 ----------------------------


def test_build_eval_feedback_uses_llm_when_payload_present(monkeypatch):
    """payload + factor_id 给齐 → 走 LLM 路径。"""
    def _fake_diag(**kw):
        return "📋 LLM 路径回的诊断"

    monkeypatch.setattr(
        "backend.services.llm_eval_diagnose.diagnose_with_llm", _fake_diag,
    )

    out = eval_service._build_eval_feedback(
        {"ic_mean": 0.05},
        payload={"ic_series": [0.05]},
        hypothesis="x",
        factor_id="f1",
    )
    assert "LLM 路径回的诊断" in out


def test_build_eval_feedback_falls_back_to_rule_when_llm_fails(monkeypatch):
    """LLM 抛异常 → 回落规则版（断言规则版的关键字符串出现）。"""
    def _boom(**kw):
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(
        "backend.services.llm_eval_diagnose.diagnose_with_llm", _boom,
    )

    out = eval_service._build_eval_feedback(
        {
            "ic_mean": 0.06,
            "ic_ir": 0.8,
            "long_short_sharpe": 1.5,
            "long_short_annret": 0.18,
            "rank_ic_mean": 0.05,
            "turnover_mean": 0.3,
        },
        payload={"ic_series": [0.06]},
        hypothesis="x",
        factor_id="f1",
    )
    # 规则版关键标志：✅ IC 显著
    assert "IC 显著" in out


def test_build_eval_feedback_skips_llm_when_payload_missing():
    """没传 payload 直接走规则版（不调 LLM；防止外部调用方少传字段时悄悄进 LLM）。"""
    out = eval_service._build_eval_feedback(
        {
            "ic_mean": 0.005,
            "ic_ir": 0.1,
            "long_short_sharpe": -0.5,
            "long_short_annret": -0.05,
            "rank_ic_mean": 0.005,
            "turnover_mean": 0.2,
        },
    )
    assert "IC 偏弱" in out
