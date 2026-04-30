"""``factor_assistant`` 单测：覆盖 prompt / JSON 校验 / AST 校验 / 落盘 / router 映射。

刻意不真的调 LLM——用 monkeypatch 把 ``_call_openai_compatible`` 换成桩，
这样测试不依赖网络 / API key / 配额，也能跑得快。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import factor_assistant as fa
from backend.services.factor_assistant import (
    FactorAssistantError,
    _build_user_prompt,
    _parse_llm_json,
    _save_factor_file,
    _validate_code_ast,
    _validate_llm_payload,
)


# ---------------------------- _build_user_prompt ----------------------------


def test_build_user_prompt_without_hints_is_concise():
    out = _build_user_prompt("过去 20 日收益率反转", None)
    # 不加 "补充信息" 段；但必须提醒 JSON 输出
    assert "过去 20 日收益率反转" in out
    assert "补充信息" not in out
    assert "JSON" in out


def test_build_user_prompt_includes_hints_when_present():
    out = _build_user_prompt("过去 20 日收益率反转", "用对数收益")
    assert "补充信息：用对数收益" in out


# ---------------------------- _parse_llm_json ----------------------------


def test_parse_llm_json_plain_object():
    got = _parse_llm_json('{"factor_id": "x_y", "code": "pass"}')
    assert got["factor_id"] == "x_y"


def test_parse_llm_json_strips_markdown_fence():
    """部分中转不支持 JSON mode，会把 JSON 包在 ```json 里返回——兜底剥掉。"""
    raw = '```json\n{"factor_id": "x_y"}\n```'
    got = _parse_llm_json(raw)
    assert got["factor_id"] == "x_y"


def test_parse_llm_json_rejects_non_object():
    with pytest.raises(FactorAssistantError, match="不是对象"):
        _parse_llm_json('[1, 2, 3]')


def test_parse_llm_json_rejects_bad_json():
    with pytest.raises(FactorAssistantError, match="不是合法 JSON"):
        _parse_llm_json("not json at all")


# ---------------------------- _validate_llm_payload ----------------------------


def _good_payload(**overrides) -> dict:
    """返回一份最小合法 payload；测试里按需 override 某个字段。"""
    base = {
        "factor_id": "my_factor_test",
        "display_name": "测试因子",
        "category": "momentum",
        "description": "一个测试用的因子",
        "hypothesis": "动量延续假设；机制是机构惯性；趋势末段失效。",
        "default_params": {"window": 20},
        "code": "class X(BaseFactor): pass\n",
    }
    base.update(overrides)
    return base


def test_validate_payload_happy_path():
    out = _validate_llm_payload(_good_payload())
    assert out["factor_id"] == "my_factor_test"
    assert out["category"] == "momentum"
    assert out["hypothesis"]  # 非空


def test_validate_payload_rejects_missing_field():
    bad = _good_payload()
    del bad["category"]
    with pytest.raises(FactorAssistantError, match="缺少字段"):
        _validate_llm_payload(bad)


def test_validate_payload_rejects_missing_hypothesis():
    """hypothesis 是 RD-Agent 借鉴的一等公民字段，缺失视为 LLM 输出不合规。"""
    bad = _good_payload()
    del bad["hypothesis"]
    with pytest.raises(FactorAssistantError, match="缺少字段"):
        _validate_llm_payload(bad)


def test_validate_payload_rejects_empty_hypothesis():
    """hypothesis 字段存在但为空白——同样拒绝（防 LLM 偷懒）。"""
    with pytest.raises(FactorAssistantError, match="hypothesis 字段不能为空"):
        _validate_llm_payload(_good_payload(hypothesis="   "))


def test_validate_payload_truncates_long_hypothesis():
    """超长 hypothesis 截到 500 字符（防 LLM 灌大段说明文）。"""
    long = "a" * 1000
    out = _validate_llm_payload(_good_payload(hypothesis=long))
    assert len(out["hypothesis"]) == 500


def test_validate_payload_rejects_bad_factor_id():
    with pytest.raises(FactorAssistantError, match="factor_id 不合法"):
        _validate_llm_payload(_good_payload(factor_id="Bad-ID"))


def test_validate_payload_rejects_unknown_category():
    with pytest.raises(FactorAssistantError, match="category 非法"):
        _validate_llm_payload(_good_payload(category="foobar"))


def test_validate_payload_rejects_empty_code():
    with pytest.raises(FactorAssistantError, match="code 字段为空"):
        _validate_llm_payload(_good_payload(code="   "))


# ---------------------------- _validate_code_ast ----------------------------


_GOOD_CODE = '''\
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Foo(BaseFactor):
    factor_id = "foo"
    display_name = "Foo"
    category = "momentum"
    description = "x"
    default_params = {}
    params_schema = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        return pd.DataFrame()
'''


def test_validate_code_ast_happy_path():
    _validate_code_ast(_GOOD_CODE)


def test_validate_code_ast_rejects_syntax_error():
    with pytest.raises(FactorAssistantError, match="语法错误"):
        _validate_code_ast("class Foo(BaseFactor")


def _inject_after_future(extra_line: str) -> str:
    """在 ``from __future__ import annotations`` 之后插入一行额外导入。

    Python 语法规定 ``from __future__`` 必须出现在文件头部，不能在它前面再塞 import；
    所以测试注入坏 import 只能插在这之后。
    """
    future_line = "from __future__ import annotations"
    assert future_line in _GOOD_CODE
    return _GOOD_CODE.replace(
        future_line, future_line + "\n\n" + extra_line, 1
    )


def test_validate_code_ast_rejects_forbidden_import_os():
    bad = _inject_after_future("import os")
    with pytest.raises(FactorAssistantError, match="不允许的 import"):
        _validate_code_ast(bad)


def test_validate_code_ast_rejects_forbidden_from_import():
    bad = _inject_after_future("from subprocess import run")
    with pytest.raises(FactorAssistantError, match="不允许的 import"):
        _validate_code_ast(bad)


def test_validate_code_ast_rejects_relative_import():
    bad = _inject_after_future("from . import something")
    with pytest.raises(FactorAssistantError, match="相对导入"):
        _validate_code_ast(bad)


def test_validate_code_ast_rejects_backend_other_module():
    """``from backend.config import settings`` 被拦——只允许 backend.factors.base。"""
    bad = _inject_after_future("from backend.config import settings")
    with pytest.raises(FactorAssistantError, match="不允许的 import"):
        _validate_code_ast(bad)


def test_validate_code_ast_rejects_exec_call():
    bad = _GOOD_CODE.replace("return pd.DataFrame()", "exec('x=1')\n        return pd.DataFrame()")
    with pytest.raises(FactorAssistantError, match="禁止调用 exec"):
        _validate_code_ast(bad)


def test_validate_code_ast_rejects_dunder_import_attr():
    bad = _GOOD_CODE.replace(
        "return pd.DataFrame()",
        "x = ctx.__import__\n        return pd.DataFrame()",
    )
    with pytest.raises(FactorAssistantError, match="__import__"):
        _validate_code_ast(bad)


def test_validate_code_ast_rejects_missing_base_factor():
    bad = _GOOD_CODE.replace("class Foo(BaseFactor):", "class Foo:")
    with pytest.raises(FactorAssistantError, match="BaseFactor"):
        _validate_code_ast(bad)


# ---------------------------- _save_factor_file ----------------------------


def test_save_factor_file_writes_and_rejects_overwrite(tmp_path, monkeypatch):
    """首次写成功，第二次同名必须拒绝——避免 LLM 悄悄覆盖手写改过的因子。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)

    p1 = _save_factor_file("my_new_factor", "code1\n")
    assert p1 == target_dir / "my_new_factor.py"
    assert p1.read_text(encoding="utf-8") == "code1\n"

    with pytest.raises(FactorAssistantError, match="已存在"):
        _save_factor_file("my_new_factor", "code2\n")


def test_save_factor_file_appends_newline(tmp_path, monkeypatch):
    """code 原文若没末尾换行，落盘时应补一个——避免 lint / git 烦。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)

    p = _save_factor_file("no_newline", "x = 1")
    assert p.read_text(encoding="utf-8").endswith("\n")


# ---------------------------- negate_factor ----------------------------


_SAMPLE_REVERSAL_SRC = '''\
"""示例反转因子。"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class ExampleReversal20(BaseFactor):
    factor_id = "example_reversal_20"
    display_name = "20日反转"
    category = "reversal"
    description = "过去 20 日累计收益率的负值。"
    hypothesis = "短期反转假设——值越大未来 1 日收益越正。"
    default_params = {"window": 20}
    params_schema = {"window": {"type": "int", "default": 20, "min": 5, "max": 60}}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return int(params.get("window", 20)) + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", 20))
        close = ctx.data.load_panel(
            ctx.symbols, ctx.start_date.date(), ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        return -close.pct_change(window).loc[ctx.start_date:]
'''


def test_negate_factor_renames_factor_id_and_class(tmp_path, monkeypatch):
    """negate 后 factor_id / 类名带 _neg 后缀；原文件不动；新文件落盘。

    断言不绑定 ast.unparse 的引号风格：只要"新 factor_id 字面量"出现即可。
    """
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    target_dir.mkdir(parents=True)

    new_factor_id, new_code = fa.negate_factor_source(
        "example_reversal_20", _SAMPLE_REVERSAL_SRC,
    )
    assert new_factor_id == "example_reversal_20_neg"
    # ast.unparse 用单引号；不绑定具体引号风格，只断关键字符串
    assert "example_reversal_20_neg" in new_code
    # 类名也变
    assert "class ExampleReversal20Neg(BaseFactor)" in new_code
    # display_name 加"（取负）"标记
    assert "（取负）" in new_code
    # hypothesis 加方向反转说明
    assert "已取负" in new_code or "方向反转" in new_code


def test_negate_factor_wraps_compute_returns():
    """compute 方法里所有 return 表达式被包了一层 USub；其它方法 return 不动。

    ast.unparse 会把 ``UnaryOp(USub, X)`` 渲染成 ``-X``（去掉冗余括号），
    所以"原本 ``return X``"变成 ``return -X``、"原本 ``return -X``"变成
    ``return --X``（语义=X，正是反向因子的设计）。required_warmup 等其它
    方法的 return 不在 compute 内不会被包。
    """
    _, new_code = fa.negate_factor_source(
        "example_reversal_20", _SAMPLE_REVERSAL_SRC,
    )
    # 原本 `return pd.DataFrame()` 被包 → `return -pd.DataFrame()`
    assert "return -pd.DataFrame()" in new_code
    # 原本 `return -close.pct_change(...)` 被再次包 → `return --close.pct_change(...)`
    assert "return --close.pct_change" in new_code
    # required_warmup 的 `return int(params...) + 5` 不应被包（不在 compute 里）
    assert "return int(params" in new_code  # 原样保留
    assert "return -int(params" not in new_code


def test_negate_factor_rejects_existing_target(tmp_path, monkeypatch):
    """落盘时新文件已存在 → 抛 FactorAssistantError。"""
    target_dir = tmp_path / "llm_generated"
    target_dir.mkdir(parents=True)
    (target_dir / "example_reversal_20_neg.py").write_text("# placeholder\n")
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)

    with pytest.raises(FactorAssistantError, match="已存在"):
        fa.negate_factor_save("example_reversal_20", _SAMPLE_REVERSAL_SRC)


def test_negate_factor_save_writes_file(tmp_path, monkeypatch):
    """end-to-end：保存到 _LLM_FACTORS_DIR 并返回 GeneratedFactor。"""
    target_dir = tmp_path / "llm_generated"
    target_dir.mkdir(parents=True)
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)

    gen = fa.negate_factor_save("example_reversal_20", _SAMPLE_REVERSAL_SRC)
    assert gen.factor_id == "example_reversal_20_neg"
    saved = target_dir / "example_reversal_20_neg.py"
    assert saved.exists()
    content = saved.read_text()
    assert "example_reversal_20_neg" in content
    # 落盘的代码必须能通过 AST 校验（保证可 import）
    fa._validate_code_ast(content)


# ---------------------------- translate_and_save (mock LLM) ----------------------------


def test_translate_and_save_end_to_end(tmp_path, monkeypatch):
    """整条链路：mock 掉 LLM 调用 + 落盘目录，看 service 是否能把合法 payload 变成文件。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    payload = {
        "factor_id": "my_mocked",
        "display_name": "Mock 因子",
        "category": "momentum",
        "description": "mock",
        "hypothesis": "测试假设：方向 + 机制 + 适用前提。",
        "default_params": {"window": 10},
        "code": _GOOD_CODE,
    }
    monkeypatch.setattr(fa, "_call_openai_compatible", lambda msgs: json.dumps(payload))

    gen = fa.translate_and_save("测试描述", None)
    assert gen.factor_id == "my_mocked"
    assert gen.hypothesis.startswith("测试假设")
    assert Path(gen.saved_path).exists()
    assert Path(gen.saved_path).parent == target_dir


def test_translate_and_save_empty_description_fails(monkeypatch):
    """空 description 不应该发 LLM——service 层立刻拒。"""
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")
    # 如果这个被调了就是 bug
    monkeypatch.setattr(
        fa, "_call_openai_compatible", lambda msgs: pytest.fail("不应触发 LLM 调用")
    )
    with pytest.raises(FactorAssistantError, match="description 不能为空"):
        fa.translate_and_save("   ", None)


def test_translate_and_save_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr(fa.settings, "openai_api_key", "")
    with pytest.raises(FactorAssistantError, match="OPENAI_API_KEY"):
        fa.translate_and_save("some factor", None)


# ---------------------------- L2.D _build_evolve_description ----------------------------


def test_build_evolve_description_includes_metrics_and_feedback():
    """评估指标 + feedback + extra_hint 都应进 description（自然语言）。"""
    desc = fa._build_evolve_description(
        parent_factor_id="foo",
        parent_hypothesis="短期反转假设",
        eval_ctx={
            "feedback_text": "📋 IC 偏弱",
            "ic_mean": 0.012, "ic_ir": 0.31,
            "long_short_sharpe": 0.4, "long_short_annret": 0.06,
            "turnover_mean": 0.45,
        },
        extra_hint="想要更短窗口",
    )
    assert "foo" in desc
    assert "短期反转假设" in desc
    assert "0.012" in desc or "0.0120" in desc
    assert "IC 偏弱" in desc
    assert "想要更短窗口" in desc


def test_build_evolve_description_no_code_content():
    """description 是纯自然语言，绝不应含 ``class X(BaseFactor)`` 等代码片段
    （这是 evolve 立即 502 的诊断重点——含代码的请求会被中转拒）。"""
    desc = fa._build_evolve_description(
        parent_factor_id="foo",
        parent_hypothesis="x",
        eval_ctx={"feedback_text": "y"},
        extra_hint=None,
    )
    assert "class " not in desc
    assert "def " not in desc
    assert "import " not in desc
    assert "```" not in desc


def test_build_evolve_description_handles_missing_fields():
    """父代 hypothesis / eval_ctx / extra_hint 全空时也不抛错，给降级文案。"""
    desc = fa._build_evolve_description(
        parent_factor_id="foo",
        parent_hypothesis="",
        eval_ctx={},
        extra_hint=None,
    )
    assert "foo" in desc
    assert "未填" in desc or "（无）" in desc


# ---------------------------- L2.D evolve_factor ----------------------------


def _good_evolve_response(factor_id: str = "child_evo2") -> str:
    """构造一份合规的 evolve LLM 响应（factor_id 后端会改写，这里随便填）。"""
    return json.dumps({
        "factor_id": factor_id,
        "display_name": "测试 v2",
        "category": "momentum",
        "description": "v2 改了窗口",
        "hypothesis": "v2 调整：缩窗口 + EMA 平滑",
        "default_params": {"window": 10},
        "code": _GOOD_CODE,
    }, ensure_ascii=False)


def test_evolve_factor_renames_factor_id_to_root_evo_n(tmp_path, monkeypatch):
    """evolve 后 factor_id 强制 = <root>_evo<gen+1>，无视 LLM 输出的名字。"""
    target_dir = tmp_path / "llm_generated"
    target_dir.mkdir(parents=True)
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    # mock parent meta：root=foo_root, generation=2 → 期望 new_factor_id=foo_root_evo3
    def _fake_meta(parent_id):
        return {
            "factor_id": parent_id,
            "hypothesis": "原假设",
            "generation": 2,
            "root_factor_id": "foo_root",
        }
    monkeypatch.setattr(fa, "_read_factor_meta_for_evolve", _fake_meta)

    # mock eval ctx 空
    monkeypatch.setattr(fa, "_read_eval_context", lambda _r: {})

    # LLM 给个故意"错"的 factor_id 看看会不会被改写
    # **kw 兼容 evolve 路径传的 reasoning_effort kwarg
    monkeypatch.setattr(
        fa, "_call_openai_compatible",
        lambda msgs, **kw: _good_evolve_response(factor_id="totally_wrong_name"),
    )

    gen = fa.evolve_factor(
        parent_factor_id="foo_root_evo2",
        parent_source_code=_GOOD_CODE,
    )
    assert gen.factor_id == "foo_root_evo3"
    saved = target_dir / "foo_root_evo3.py"
    assert saved.exists()
    # 落盘的代码里 factor_id 已被改写成 foo_root_evo3
    content = saved.read_text()
    assert "foo_root_evo3" in content
    assert "totally_wrong_name" not in content


def test_evolve_factor_includes_eval_feedback_in_prompt(tmp_path, monkeypatch):
    """parent_eval_run_id 给定 → prompt 携带 feedback_text + 关键 metrics。"""
    target_dir = tmp_path / "llm_generated"
    target_dir.mkdir(parents=True)
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(fa, "_read_factor_meta_for_evolve", lambda _: {
        "factor_id": "foo", "hypothesis": "h", "generation": 1, "root_factor_id": "foo",
    })
    monkeypatch.setattr(fa, "_read_eval_context", lambda _r: {
        "feedback_text": "📋 IC 偏弱，建议改 EMA 平滑",
        "ic_mean": 0.012, "ic_ir": 0.31, "long_short_sharpe": 0.4,
        "long_short_annret": 0.06, "turnover_mean": 0.45,
    })

    captured: dict = {}

    def _capture(msgs, **_kw):  # **_kw 兼容 evolve 路径的 reasoning_effort
        captured["msgs"] = msgs
        return _good_evolve_response()

    monkeypatch.setattr(fa, "_call_openai_compatible", _capture)

    fa.evolve_factor(
        parent_factor_id="foo",
        parent_source_code=_GOOD_CODE,
        parent_eval_run_id="run_xyz",
        extra_hint="想要更短窗口",
    )
    user_prompt = captured["msgs"][-1]["content"]
    assert "IC 偏弱" in user_prompt
    assert "0.0120" in user_prompt or "0.012" in user_prompt  # IC mean
    assert "想要更短窗口" in user_prompt


def test_evolve_factor_propagates_loop_failure(tmp_path, monkeypatch):
    """LLM 始终返回不合规代码 → evolve 抛 FactorAssistantError "反馈循环 N 轮仍失败"。"""
    target_dir = tmp_path / "llm_generated"
    target_dir.mkdir(parents=True)
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(fa, "_TRANSLATE_MAX_RETRIES", 1)  # 共 2 次尝试
    monkeypatch.setattr(fa, "_read_factor_meta_for_evolve", lambda _: {
        "factor_id": "foo", "hypothesis": "h", "generation": 1, "root_factor_id": "foo",
    })
    monkeypatch.setattr(fa, "_read_eval_context", lambda _r: {})

    bad_code = _GOOD_CODE.replace(
        "from __future__ import annotations",
        "from __future__ import annotations\n\nimport os",
        1,
    )
    monkeypatch.setattr(
        fa, "_call_openai_compatible",
        lambda msgs, **_kw: _good_evolve_response(factor_id="x").replace(
            json.dumps(_GOOD_CODE), json.dumps(bad_code),
        ),
    )

    with pytest.raises(FactorAssistantError, match="反馈循环.*仍失败"):
        fa.evolve_factor(parent_factor_id="foo", parent_source_code=_GOOD_CODE)


# ---------------------------- L2.B 反馈循环 ----------------------------


def _good_payload_json(code: str = _GOOD_CODE) -> str:
    """构造一份合规的 LLM 响应字符串。"""
    return json.dumps({
        "factor_id": "loop_factor_test",
        "display_name": "loop test",
        "category": "momentum",
        "description": "loop test",
        "hypothesis": "test 循环",
        "default_params": {},
        "code": code,
    }, ensure_ascii=False)


def test_translate_retries_after_ast_failure_then_succeeds(tmp_path, monkeypatch):
    """第 1 轮 LLM 返回坏 import（AST fail），第 2 轮返回好代码 → 成功落盘。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    # 第 1 轮坏代码：插入 `import os`（白名单外）
    bad_code = _GOOD_CODE.replace(
        "from __future__ import annotations",
        "from __future__ import annotations\n\nimport os",
        1,
    )
    bad_response = _good_payload_json(bad_code)
    good_response = _good_payload_json(_GOOD_CODE)

    call_log: list[list[dict]] = []

    def _fake_call(messages):
        call_log.append([{"role": m["role"]} for m in messages])
        return bad_response if len(call_log) == 1 else good_response

    monkeypatch.setattr(fa, "_call_openai_compatible", _fake_call)

    gen = fa.translate_and_save("循环测试", None)
    assert gen.factor_id == "loop_factor_test"
    # 至少 2 次调用：第一次失败、第二次成功
    assert len(call_log) >= 2
    # 第二次的 messages 必含 assistant + user 重试消息（system + user + asst + user_retry）
    assert len(call_log[1]) >= 4
    assert call_log[1][2]["role"] == "assistant"
    assert call_log[1][3]["role"] == "user"


def test_translate_gives_up_after_max_retries(tmp_path, monkeypatch):
    """LLM 始终返回坏代码 → 超过 max_retries 后抛 FactorAssistantError。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(fa, "_TRANSLATE_MAX_RETRIES", 2)  # 共 3 次尝试

    bad_code = _GOOD_CODE.replace(
        "from __future__ import annotations",
        "from __future__ import annotations\n\nimport os",
        1,
    )
    bad_response = _good_payload_json(bad_code)

    call_count = {"n": 0}

    def _fake_call(messages):
        call_count["n"] += 1
        return bad_response

    monkeypatch.setattr(fa, "_call_openai_compatible", _fake_call)

    with pytest.raises(FactorAssistantError, match="反馈循环.*仍失败"):
        fa.translate_and_save("循环测试 fail", None)
    assert call_count["n"] == 3  # max_retries=2 + 1 = 3 次尝试


def test_translate_does_not_retry_network_error(tmp_path, monkeypatch):
    """网络层错误不应触发重试（节省 token + 避免对环境问题盲目放大调用）。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    call_count = {"n": 0}

    def _fake_call(messages):
        call_count["n"] += 1
        raise FactorAssistantError("调用 LLM 失败（网络层）：mock")

    monkeypatch.setattr(fa, "_call_openai_compatible", _fake_call)

    with pytest.raises(FactorAssistantError, match="网络层"):
        fa.translate_and_save("net error", None)
    assert call_count["n"] == 1  # 只调一次，不重试


def test_translate_does_not_retry_missing_api_key(monkeypatch):
    """OPENAI_API_KEY 缺失也是环境问题，不应重试。"""
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    call_count = {"n": 0}

    def _fake_call(messages):
        call_count["n"] += 1
        raise FactorAssistantError("OPENAI_API_KEY 未设置")

    monkeypatch.setattr(fa, "_call_openai_compatible", _fake_call)

    with pytest.raises(FactorAssistantError, match="OPENAI_API_KEY"):
        fa.translate_and_save("key missing", None)
    assert call_count["n"] == 1


def test_translate_does_not_retry_upstream_5xx(tmp_path, monkeypatch):
    """LLM 上游 502/4xx 是中转/部署问题，不应触发反馈循环重试。

    回归历史 bug：曾把 "返回错误状态" 当作可重试错，导致 retry 路径里 raw
    没赋值 → UnboundLocalError 500。
    """
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    call_count = {"n": 0}

    def _fake_call(messages):
        call_count["n"] += 1
        raise FactorAssistantError("LLM 返回错误状态 502；详情请看后端日志")

    monkeypatch.setattr(fa, "_call_openai_compatible", _fake_call)

    with pytest.raises(FactorAssistantError, match="返回错误状态 502"):
        fa.translate_and_save("upstream down", None)
    assert call_count["n"] == 1  # 一次失败就抛，不重试


def test_translate_does_not_retry_non_json_response(tmp_path, monkeypatch):
    """LLM 返回非 JSON（base_url 漏 /v1 等）也是部署错配，不应重试。"""
    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    call_count = {"n": 0}

    def _fake_call(messages):
        call_count["n"] += 1
        raise FactorAssistantError(
            "上游不是 JSON 响应（content-type='text/html'）..."
        )

    monkeypatch.setattr(fa, "_call_openai_compatible", _fake_call)

    with pytest.raises(FactorAssistantError, match="不是 JSON"):
        fa.translate_and_save("misconfigured base url", None)
    assert call_count["n"] == 1


# ---------------------------- router 错误映射 ----------------------------


def test_router_maps_missing_key_to_503(monkeypatch):
    """未配 key → 503。"""
    from fastapi.testclient import TestClient

    from backend.api.main import app

    monkeypatch.setattr(fa.settings, "openai_api_key", "")
    with TestClient(app) as c:
        r = c.post(
            "/api/factor_assistant/translate",
            json={"description": "过去 20 日反转因子"},
        )
    assert r.status_code == 503
    assert r.json()["code"] == 503


def test_router_maps_validation_error_to_400(tmp_path, monkeypatch):
    """LLM 输出 category 非法 → service 抛 FactorAssistantError → router 映射 400。"""
    from fastapi.testclient import TestClient

    from backend.api.main import app

    target_dir = tmp_path / "llm_generated"
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")
    # 返回一个 category 非法的 payload
    bad_payload = {
        "factor_id": "x_y",
        "display_name": "X",
        "category": "SOMETHING_BAD",
        "description": "x",
        "hypothesis": "测试假设。",
        "default_params": {},
        "code": _GOOD_CODE,
    }
    monkeypatch.setattr(
        fa, "_call_openai_compatible", lambda msgs: json.dumps(bad_payload)
    )

    with TestClient(app) as c:
        r = c.post(
            "/api/factor_assistant/translate",
            json={"description": "过去 20 日反转因子"},
        )
    assert r.status_code == 400
    assert "category" in r.json()["message"]


def test_router_maps_existing_file_to_409(tmp_path, monkeypatch):
    """文件已存在 → 409。"""
    from fastapi.testclient import TestClient

    from backend.api.main import app

    target_dir = tmp_path / "llm_generated"
    target_dir.mkdir(parents=True)
    (target_dir / "dup_factor.py").write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target_dir)
    monkeypatch.setattr(fa.settings, "openai_api_key", "sk-test")

    payload = {
        "factor_id": "dup_factor",
        "display_name": "Dup",
        "category": "momentum",
        "description": "x",
        "hypothesis": "测试假设。",
        "default_params": {},
        "code": _GOOD_CODE,
    }
    monkeypatch.setattr(
        fa, "_call_openai_compatible", lambda msgs: json.dumps(payload)
    )

    with TestClient(app) as c:
        r = c.post(
            "/api/factor_assistant/translate",
            json={"description": "又一个测试因子"},
        )
    assert r.status_code == 409
