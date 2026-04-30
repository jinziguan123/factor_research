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
