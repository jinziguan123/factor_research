"""factor_assistant Phase 2(evolve)编排 + 关键纯函数测试。

评估闭环(Phase 0 生成 → Phase 1 auto_eval → Phase 2 evolve)前后端已实现且实战
跑通(evolve_factor 里有"绕开中转 502"的修正注释)。但编排逻辑此前无单元测试——
和 run_backtest 同样的盲区。这里补 evolve 核心编排 + 两个纯函数,验证无变量引用/
逻辑 bug + 防回归;LLM / DB 用 monkeypatch 隔离,不依赖外部。

    uv run pytest backend/tests/test_factor_assistant_evolve.py -v
"""
from __future__ import annotations

import pytest

from backend.services import factor_assistant as fa

_SAMPLE_CODE = '''\
from backend.factors.base import BaseFactor, FactorContext
import pandas as pd


class MyMomentum(BaseFactor):
    factor_id = "llm_momo"
    display_name = "动量"
    category = "momentum"
    hypothesis = "过去 N 日涨得多的继续涨"
    default_params = {"window": 20}

    def required_warmup(self, params):
        return int(params.get("window", 20))

    def compute(self, ctx, params):
        return pd.DataFrame()
'''


# ---------------------------- _build_evolve_description（纯函数）----------------------------


def test_build_evolve_description_includes_context_excludes_code():
    eval_ctx = {
        "feedback_text": "IC 偏低，建议拉长窗口",
        "ic_mean": 0.012, "ic_ir": 0.45,
        "long_short_sharpe": 0.8, "long_short_annret": 0.15,
        "turnover_mean": 0.6,
    }
    desc = fa._build_evolve_description(
        parent_factor_id="llm_momo",
        parent_hypothesis="过去 N 日涨得多的继续涨",
        eval_ctx=eval_ctx,
        extra_hint="想要更短窗口",
    )
    assert "llm_momo" in desc
    assert "过去 N 日涨得多" in desc
    assert "IC mean=0.0120" in desc
    assert "IC 偏低" in desc
    assert "想要更短窗口" in desc
    # 关键设计：描述里绝不能含父代源码（否则触发中转 502）
    assert "class " not in desc
    assert "def compute" not in desc


def test_build_evolve_description_handles_missing_context():
    desc = fa._build_evolve_description(
        parent_factor_id="x_factor", parent_hypothesis="",
        eval_ctx={}, extra_hint=None,
    )
    assert "（无）" in desc      # 指标/反馈/指令缺失兜底
    assert "（未填）" in desc    # 假设未填兜底


# ---------------------------- _force_factor_id（AST 改写）----------------------------


def test_force_factor_id_rewrites_attribute():
    out = fa._force_factor_id(_SAMPLE_CODE, "llm_momo_evo2")
    assert "llm_momo_evo2" in out
    compile(out, "<evolved>", "exec")  # 改写后仍可编译


def test_force_factor_id_raises_when_no_basefactor():
    with pytest.raises(fa.FactorAssistantError):
        fa._force_factor_id("x = 1\n", "whatever")


def test_force_factor_id_raises_on_syntax_error():
    with pytest.raises(fa.FactorAssistantError):
        fa._force_factor_id("class Broken(BaseFactor:\n", "x")


# ---------------------------- evolve_factor（编排，mock LLM/DB）----------------------------


def test_evolve_factor_orchestration(monkeypatch, tmp_path):
    # 父代元数据：root=llm_momo，当前最大代号 2 → 新代号应为 3
    monkeypatch.setattr(fa, "_read_factor_meta_for_evolve", lambda pid: {
        "factor_id": "llm_momo", "hypothesis": "过去 N 日涨得多的继续涨",
        "generation": 1, "max_generation_in_lineage": 2,
        "root_factor_id": "llm_momo",
    })
    monkeypatch.setattr(fa, "_read_eval_context", lambda rid: {
        "feedback_text": "IC 偏低", "ic_mean": 0.01, "ic_ir": 0.4,
        "long_short_sharpe": None, "long_short_annret": None, "turnover_mean": None,
    })

    captured: dict = {}

    def fake_translate(description, hints=None, images=None):
        captured["description"] = description
        return {
            "factor_id": "whatever_llm_picked",  # 应被强制改写
            "display_name": "动量改进版", "category": "momentum",
            "description": "改进版", "hypothesis": "改进假设",
            "default_params": {"window": 10}, "code": _SAMPLE_CODE,
        }

    monkeypatch.setattr(fa, "_translate_to_payload", fake_translate)
    saved_path = tmp_path / "llm_momo_evo3.py"
    monkeypatch.setattr(fa, "_save_factor_file", lambda fid, code: saved_path)

    gen = fa.evolve_factor(
        parent_factor_id="llm_momo", parent_source_code="(ignored)",
        parent_eval_run_id="run123", extra_hint="更短窗口",
    )

    # 命名规则：<root>_evo<max_gen+1>
    assert gen.factor_id == "llm_momo_evo3"
    # 编排确实把父代假设 + 评估反馈 + 用户指令喂进了 LLM 的 description
    assert "过去 N 日涨得多" in captured["description"]
    assert "IC 偏低" in captured["description"]
    assert "更短窗口" in captured["description"]
    # 生成代码里的 factor_id 被强制改写为新 id（LLM 自起的名被覆盖）
    assert "llm_momo_evo3" in gen.code
    assert gen.saved_path == str(saved_path)


def test_evolve_factor_without_eval_context(monkeypatch, tmp_path):
    # 不传 parent_eval_run_id 时仍能进化（description 里反馈/指标为"（无）"）
    monkeypatch.setattr(fa, "_read_factor_meta_for_evolve", lambda pid: {
        "factor_id": "llm_momo", "hypothesis": "动量假设",
        "generation": 1, "max_generation_in_lineage": 1, "root_factor_id": "llm_momo",
    })
    captured: dict = {}

    def fake_translate(description, hints=None, images=None):
        captured["description"] = description
        return {
            "factor_id": "x", "display_name": "d", "category": "momentum",
            "description": "改进版", "hypothesis": "h",
            "default_params": {}, "code": _SAMPLE_CODE,
        }

    monkeypatch.setattr(fa, "_translate_to_payload", fake_translate)
    monkeypatch.setattr(fa, "_save_factor_file", lambda fid, code: tmp_path / f"{fid}.py")

    gen = fa.evolve_factor(
        parent_factor_id="llm_momo", parent_source_code="",
        parent_eval_run_id=None, extra_hint=None,
    )
    assert gen.factor_id == "llm_momo_evo2"
    assert "动量假设" in captured["description"]
