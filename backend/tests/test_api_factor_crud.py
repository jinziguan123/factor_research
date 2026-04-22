"""``/api/factors`` 写路由 (POST / PUT / DELETE) + ``_verify_class_factor_id`` /
``_is_under_llm_dir`` 辅助函数的单元测试。

刻意用 monkeypatch 绕开 MySQL（``FactorRegistry._persist_meta`` / ``unregister``）
和 watchdog，这样不用起 DB 也能测写操作的关键分支：
- 路径必须位于 ``llm_generated/`` 才允许写（路径穿越 / 软链 / 前缀匹配都挡掉）
- 类属性 ``factor_id`` 必须与 URL / 请求体一致
- AST 校验失败 → 400
- 源码落盘复用 ``factor_assistant._save_factor_file`` 的拒绝覆盖语义 → 409
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.routers import factors as factors_router


# ---------------------------- _is_under_llm_dir ----------------------------


def test_is_under_llm_dir_accepts_direct_child(tmp_path, monkeypatch):
    monkeypatch.setattr(factors_router, "_LLM_DIR", tmp_path.resolve())
    f = tmp_path / "foo.py"
    f.write_text("# x\n")
    assert factors_router._is_under_llm_dir(f) is True


def test_is_under_llm_dir_rejects_sibling_with_prefix(tmp_path, monkeypatch):
    """防 ``/.../llm_generated_evil/x.py`` 这类前缀匹配攻击——必须真在目录下。"""
    target = tmp_path / "llm_generated"
    target.mkdir()
    evil = tmp_path / "llm_generated_evil"
    evil.mkdir()
    monkeypatch.setattr(factors_router, "_LLM_DIR", target.resolve())
    f = evil / "x.py"
    f.write_text("# x\n")
    assert factors_router._is_under_llm_dir(f) is False


def test_is_under_llm_dir_rejects_parent_traversal(tmp_path, monkeypatch):
    """构造一条 ``llm_generated/../other.py`` 形式的路径，resolve 后不在 llm_generated 下。"""
    target = tmp_path / "llm_generated"
    target.mkdir()
    other = tmp_path / "other.py"
    other.write_text("# x\n")
    monkeypatch.setattr(factors_router, "_LLM_DIR", target.resolve())
    sneaky = target / ".." / "other.py"
    assert factors_router._is_under_llm_dir(sneaky) is False


# ---------------------------- _require_factor_file ----------------------------


def test_require_factor_file_accepts_llm_generated(tmp_path, monkeypatch):
    """llm_generated 下的因子应通过校验。"""
    llm = tmp_path / "llm_generated"
    llm.mkdir()
    f = llm / "my_factor.py"
    f.write_text("# x\n")
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())

    import inspect as _inspect
    from backend.runtime.factor_registry import FactorRegistry
    reg = FactorRegistry()
    # 塞一个假类进 registry,让 _factor_source_file 能定位到 f
    class _Fake:
        factor_id = "my_factor"
    monkeypatch.setattr(
        _inspect, "getsourcefile",
        lambda cls: str(f) if cls is _Fake else None,
    )
    monkeypatch.setattr(reg, "get", lambda fid: _Fake() if fid == "my_factor" else None)

    got = factors_router._require_factor_file("my_factor", reg)
    assert got.resolve() == f.resolve()


def test_require_factor_file_accepts_business_category(tmp_path, monkeypatch):
    """业务目录（momentum/reversal/oscillator 等）下的因子也应通过。"""
    momentum = tmp_path / "momentum"
    momentum.mkdir()
    f = momentum / "biz_factor.py"
    f.write_text("# x\n")
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())

    import inspect as _inspect
    from backend.runtime.factor_registry import FactorRegistry
    reg = FactorRegistry()
    class _Fake:
        factor_id = "biz_factor"
    monkeypatch.setattr(
        _inspect, "getsourcefile",
        lambda cls: str(f) if cls is _Fake else None,
    )
    monkeypatch.setattr(reg, "get", lambda fid: _Fake() if fid == "biz_factor" else None)

    got = factors_router._require_factor_file("biz_factor", reg)
    assert got.resolve() == f.resolve()


def test_require_factor_file_rejects_outside_factors_root(tmp_path, monkeypatch):
    """源码路径逃出 backend/factors/ → 403。"""
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", (tmp_path / "factors").resolve())
    outside = tmp_path / "elsewhere" / "sneak.py"
    outside.parent.mkdir()
    outside.write_text("# x\n")

    import inspect as _inspect
    from backend.runtime.factor_registry import FactorRegistry
    reg = FactorRegistry()
    class _Fake:
        factor_id = "sneak"
    monkeypatch.setattr(
        _inspect, "getsourcefile",
        lambda cls: str(outside) if cls is _Fake else None,
    )
    monkeypatch.setattr(reg, "get", lambda fid: _Fake() if fid == "sneak" else None)

    with pytest.raises(HTTPException) as excinfo:
        factors_router._require_factor_file("sneak", reg)
    assert excinfo.value.status_code == 403
    assert "backend/factors" in str(excinfo.value.detail)


def test_require_factor_file_rejects_non_py_suffix(tmp_path, monkeypatch):
    """源码文件不是 .py（理论上 inspect.getsourcefile 不会给这种，防御性）→ 400。"""
    momentum = tmp_path / "momentum"
    momentum.mkdir()
    f = momentum / "weird.txt"
    f.write_text("# x\n")
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())

    import inspect as _inspect
    from backend.runtime.factor_registry import FactorRegistry
    reg = FactorRegistry()
    class _Fake:
        factor_id = "weird"
    monkeypatch.setattr(
        _inspect, "getsourcefile",
        lambda cls: str(f) if cls is _Fake else None,
    )
    monkeypatch.setattr(reg, "get", lambda fid: _Fake() if fid == "weird" else None)

    with pytest.raises(HTTPException) as excinfo:
        factors_router._require_factor_file("weird", reg)
    assert excinfo.value.status_code == 400
    assert ".py" in str(excinfo.value.detail)


# ---------------------------- _save_backup ----------------------------


def test_save_backup_copies_file_with_timestamp(tmp_path, monkeypatch):
    """备份产物:路径在 _FACTORS_ROOT/.backup/、文件名带时间戳、内容与源一致。"""
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())
    src = tmp_path / "momentum" / "foo.py"
    src.parent.mkdir()
    src.write_text("# original content\n")

    backup = factors_router._save_backup(src, "foo")

    assert backup is not None
    assert backup.parent == tmp_path.resolve() / ".backup"
    assert backup.name.startswith("foo.") and backup.name.endswith(".py")
    # 时间戳部分是 yyyyMMdd-HHmmss,长度固定 15
    ts_part = backup.stem.split(".", 1)[1]
    assert len(ts_part) == 15
    assert backup.read_text() == "# original content\n"


def test_save_backup_returns_none_when_source_missing(tmp_path, monkeypatch):
    """源文件不存在（新因子首次 PUT 的防御分支）→ 返回 None,不报错。"""
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())
    src = tmp_path / "does_not_exist.py"

    got = factors_router._save_backup(src, "ghost")

    assert got is None
    assert not (tmp_path / ".backup").exists()  # 备份目录也不该被创建


def test_save_backup_keeps_only_5_latest(tmp_path, monkeypatch):
    """连续备份同一 factor_id 6 次,只保留最近 5 份。"""
    import time

    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())
    src = tmp_path / "momentum" / "foo.py"
    src.parent.mkdir()
    src.write_text("v1\n")

    # 为了让文件名时间戳不同,每次 sleep 1 秒
    backups = []
    for i in range(6):
        src.write_text(f"v{i + 1}\n")
        b = factors_router._save_backup(src, "foo")
        backups.append(b)
        time.sleep(1.01)  # 确保 yyyyMMdd-HHmmss 严格递增

    backup_dir = tmp_path.resolve() / ".backup"
    remaining = sorted(backup_dir.glob("foo.*.py"))
    assert len(remaining) == 5
    # 最老那份应已被清理
    assert backups[0] not in remaining
    # 最新 5 份都在
    for b in backups[1:]:
        assert b in remaining


def test_save_backup_different_factors_dont_interfere(tmp_path, monkeypatch):
    """两个不同 factor_id 各自备份,保留策略互不影响。"""
    import time

    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())
    foo = tmp_path / "momentum" / "foo.py"
    bar = tmp_path / "reversal" / "bar.py"
    foo.parent.mkdir()
    bar.parent.mkdir()
    foo.write_text("foo1\n")
    bar.write_text("bar1\n")

    # foo 备份 3 次,bar 备份 1 次
    for i in range(3):
        foo.write_text(f"foo{i + 1}\n")
        factors_router._save_backup(foo, "foo")
        time.sleep(1.01)
    factors_router._save_backup(bar, "bar")

    backup_dir = tmp_path.resolve() / ".backup"
    assert len(list(backup_dir.glob("foo.*.py"))) == 3
    assert len(list(backup_dir.glob("bar.*.py"))) == 1


# ---------------------------- _verify_class_factor_id ----------------------------


_CODE_TMPL = '''\
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Foo(BaseFactor):
    factor_id = "{fid}"
    display_name = "Foo"
    category = "momentum"
    description = "x"
    default_params = {{}}
    params_schema = {{}}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        return pd.DataFrame()
'''


def test_verify_class_factor_id_matches():
    factors_router._verify_class_factor_id(_CODE_TMPL.format(fid="abc_def"), "abc_def")


def test_verify_class_factor_id_mismatch_raises_400():
    with pytest.raises(HTTPException) as excinfo:
        factors_router._verify_class_factor_id(
            _CODE_TMPL.format(fid="abc_def"), "other_name"
        )
    assert excinfo.value.status_code == 400
    assert "不一致" in str(excinfo.value.detail)


def test_verify_class_factor_id_missing_class_raises_400():
    """代码里根本没 BaseFactor 子类 → 400 拒绝。"""
    bad = "x = 1\n"
    with pytest.raises(HTTPException) as excinfo:
        factors_router._verify_class_factor_id(bad, "any")
    assert excinfo.value.status_code == 400


def test_verify_class_factor_id_syntax_error_raises_400():
    with pytest.raises(HTTPException) as excinfo:
        factors_router._verify_class_factor_id("class Foo(BaseFactor", "any")
    assert excinfo.value.status_code == 400
    assert "语法错误" in str(excinfo.value.detail)


# ---------------------------- 路由端到端（monkey-patched 环境） ----------------------------


@pytest.fixture
def isolated_llm_dir(tmp_path, monkeypatch):
    """把 llm_generated 目录切到 tmp_path；阻断 MySQL 与 watchdog。

    stub 版 ``scan_and_register`` 会真的用 ``importlib.util`` 把 tmp 目录里的 .py
    load 进来、发现 BaseFactor 子类、登记到内存表——否则 POST 后 ``reg.get(fid)``
    拿不到新创建的类，happy path 测不过去。
    """
    import hashlib
    import importlib.util
    import inspect as _inspect
    import sys as _sys

    from backend.engine.base_factor import BaseFactor
    from backend.services import factor_assistant as fa
    from backend.runtime import factor_registry as fr
    from backend.runtime import task_pool

    target = tmp_path / "llm_generated"
    target.mkdir()
    monkeypatch.setattr(factors_router, "_LLM_DIR", target.resolve())
    # PUT 路径现已改调 _require_factor_file(路径白名单是 _FACTORS_ROOT),
    # 把 _FACTORS_ROOT 也切到 tmp_path,保证 llm_generated 下的 tmp 文件也能通过校验。
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", tmp_path.resolve())
    monkeypatch.setattr(fa, "_LLM_FACTORS_DIR", target)

    monkeypatch.setattr(
        fr.FactorRegistry, "_persist_meta", lambda self, cls, code_hash: 1
    )

    def _stub_scan(self, root_pkg: str = "backend.factors") -> list[str]:
        """按 tmp 目录下的 .py **源码字节** 重编译 & 注册。

        不用 ``spec.loader.exec_module``：``SourceFileLoader`` 会走 ``__pycache__``
        bytecode 缓存，同一秒内两次写盘时 mtime 不变，会复用 **老字节码**，
        导致热加载路径上的 ``display_name`` 根本没更新——这正是 PUT 测试
        曾经偶发失败的根因。直接 ``compile(bytes, path, 'exec')`` 把缓存绕开。

        扫描范围：``target``（llm_generated）+ ``tmp_path`` 下的所有业务子目录
        (momentum/reversal/oscillator/...)，便于 PUT 覆写业务因子后 reload 真实生效。
        ``.backup/`` 目录跳过，避免备份文件被当成因子加载。
        """
        import types as _types

        # 扫描目录列表：llm_generated + tmp_path 下的一级子目录（业务目录）
        # tmp_path 的其他子目录没 .py 也不会出什么事,空遍历
        scan_dirs: list[Path] = [target]
        for sub in sorted(tmp_path.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name in ("llm_generated", ".backup") or sub.name.startswith("."):
                continue
            scan_dirs.append(sub)

        updated: list[str] = []
        global_idx = 0
        for scan_dir in scan_dirs:
            for py in sorted(scan_dir.glob("*.py")):
                if py.name == "__init__.py":
                    continue
                mod_name = f"_test_scan_{scan_dir.name}_{py.stem}_{global_idx}"
                global_idx += 1
                source = py.read_bytes()
                module = _types.ModuleType(mod_name)
                module.__file__ = str(py)
                try:
                    code_obj = compile(source, str(py), "exec")
                    exec(code_obj, module.__dict__)
                except Exception:  # noqa: BLE001
                    _sys.modules.pop(mod_name, None)
                    continue
                _sys.modules[mod_name] = module
                code_hash = hashlib.sha1(source).hexdigest()
                for _, obj in _inspect.getmembers(module, _inspect.isclass):
                    if obj is BaseFactor or not issubclass(obj, BaseFactor):
                        continue
                    if obj.__module__ != module.__name__:
                        continue
                    fid = getattr(obj, "factor_id", None)
                    if not fid:
                        continue
                    with self._lock:
                        if self._code_hash.get(fid) == code_hash:
                            self._classes[fid] = obj
                            continue
                        self._classes[fid] = obj
                        self._code_hash[fid] = code_hash
                        self._version[fid] = self._version.get(fid, 0) + 1
                    updated.append(fid)
        return updated

    monkeypatch.setattr(fr.FactorRegistry, "scan_and_register", _stub_scan)
    monkeypatch.setattr(
        fr.FactorRegistry, "reload_module", lambda self, mod: _stub_scan(self)
    )

    def _stub_unregister(self, factor_id):
        with self._lock:
            existed = factor_id in self._classes
            self._classes.pop(factor_id, None)
            self._code_hash.pop(factor_id, None)
            self._version.pop(factor_id, None)
        return existed

    monkeypatch.setattr(fr.FactorRegistry, "unregister", _stub_unregister)
    monkeypatch.setattr(task_pool, "reset_pool", lambda: None)
    monkeypatch.setattr(factors_router, "reset_pool", lambda: None)

    reg = fr.FactorRegistry()
    reg._classes.clear()
    reg._code_hash.clear()
    reg._version.clear()

    return target


def test_create_factor_happy_path(isolated_llm_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/factors",
            json={
                "factor_id": "crud_test_factor",
                "code": _CODE_TMPL.format(fid="crud_test_factor"),
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["factor_id"] == "crud_test_factor"
    # 文件真落盘了
    assert (isolated_llm_dir / "crud_test_factor.py").exists()


def test_create_factor_rejects_factor_id_mismatch(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/factors",
            json={
                "factor_id": "url_says_this",
                "code": _CODE_TMPL.format(fid="code_says_that"),
            },
        )
    assert r.status_code == 400
    assert "不一致" in r.json()["message"]


def test_create_factor_rejects_duplicate(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    # 先占位
    (isolated_llm_dir / "dup_x.py").write_text("# placeholder\n")
    with TestClient(app) as c:
        r = c.post(
            "/api/factors",
            json={
                "factor_id": "dup_x",
                "code": _CODE_TMPL.format(fid="dup_x"),
            },
        )
    assert r.status_code == 409


def test_create_factor_rejects_bad_factor_id(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/factors",
            json={"factor_id": "Bad-ID", "code": _CODE_TMPL.format(fid="Bad-ID")},
        )
    # 先过 pydantic / factor_id 正则后者
    assert r.status_code == 400


def test_create_factor_rejects_forbidden_import(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    bad_code = _CODE_TMPL.format(fid="import_evil").replace(
        "import pandas as pd",
        "import pandas as pd\nimport os",  # 注入禁品
    )
    with TestClient(app) as c:
        r = c.post(
            "/api/factors",
            json={"factor_id": "import_evil", "code": bad_code},
        )
    assert r.status_code == 400


# ---------------------------- PUT / DELETE / GET code 的 smoke ----------------------------


def _create_via_api(client, fid: str, code: str | None = None):
    """测试辅助：走 POST 建一个因子，返回创建后的响应，方便后续 PUT / DELETE 基于它。"""
    return client.post(
        "/api/factors",
        json={"factor_id": fid, "code": code or _CODE_TMPL.format(fid=fid)},
    )


def test_get_factor_code_returns_source(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as c:
        assert _create_via_api(c, "read_me").status_code == 200
        r = c.get("/api/factors/read_me/code")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["factor_id"] == "read_me"
    assert body["editable"] is True
    assert "class Foo(BaseFactor)" in body["code"]


def test_update_factor_code_happy_path(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as c:
        assert _create_via_api(c, "edit_me").status_code == 200
        # 把 display_name 从 "Foo" 改成 "Bar"
        new_code = _CODE_TMPL.format(fid="edit_me").replace('"Foo"', '"Bar"')
        r = c.put("/api/factors/edit_me/code", json={"code": new_code})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["display_name"] == "Bar"
    # 文件确实被覆写
    assert '"Bar"' in (isolated_llm_dir / "edit_me.py").read_text()


def test_update_factor_code_rejects_factor_id_rename(isolated_llm_dir):
    """PUT 里 URL 的 factor_id 与代码里的类属性不一致 → 400（避免隐式改名）。"""
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as c:
        assert _create_via_api(c, "stable_id").status_code == 200
        renamed = _CODE_TMPL.format(fid="renamed_id")
        r = c.put("/api/factors/stable_id/code", json={"code": renamed})
    assert r.status_code == 400
    assert "不一致" in r.json()["message"]


def test_delete_factor_removes_file_and_registry(isolated_llm_dir):
    from fastapi.testclient import TestClient

    from backend.api.main import app
    from backend.runtime.factor_registry import FactorRegistry

    with TestClient(app) as c:
        assert _create_via_api(c, "kill_me").status_code == 200
        assert (isolated_llm_dir / "kill_me.py").exists()
        r = c.delete("/api/factors/kill_me")
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] == "kill_me"
    assert not (isolated_llm_dir / "kill_me.py").exists()
    reg = FactorRegistry()
    assert "kill_me" not in reg._classes


def test_delete_rejects_non_llm_generated(isolated_llm_dir, monkeypatch):
    """构造一个注册了、但文件不在 llm_generated 下的因子 → DELETE 仍应 403。

    PUT 对业务因子现已放开,覆盖验证见 test_put_business_factor_succeeds_with_backup。
    本测试继续保留 DELETE 的 403 边界,防止删除权限意外放开。
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.runtime.factor_registry import FactorRegistry

    outside_dir = isolated_llm_dir.parent / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "external_factor.py"
    outside_file.write_text(_CODE_TMPL.format(fid="external_factor"))

    import hashlib
    import importlib.util
    import inspect as _inspect

    spec = importlib.util.spec_from_file_location(
        "_test_external_factor", outside_file
    )
    mod = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules["_test_external_factor"] = mod
    spec.loader.exec_module(mod)
    cls = next(
        obj
        for _, obj in _inspect.getmembers(mod, _inspect.isclass)
        if getattr(obj, "factor_id", None) == "external_factor"
    )
    reg = FactorRegistry()
    with reg._lock:
        reg._classes["external_factor"] = cls
        reg._code_hash["external_factor"] = hashlib.sha1(
            _inspect.getsource(cls).encode()
        ).hexdigest()
        reg._version["external_factor"] = 1

    with TestClient(app) as c:
        r_del = c.delete("/api/factors/external_factor")

    assert r_del.status_code == 403
    assert outside_file.exists()
    assert "external_factor" in reg._classes


# ---------------------------- isolated_factors_root fixture ----------------------------


@pytest.fixture
def isolated_factors_root(isolated_llm_dir, monkeypatch):
    """在 isolated_llm_dir 基础上,把 _FACTORS_ROOT 也 monkeypatch 到 tmp。

    结构:
      tmp_path/                        ← _FACTORS_ROOT
      ├── llm_generated/              ← _LLM_DIR (由 isolated_llm_dir 已设置)
      ├── momentum/                   ← 本 fixture 的调用方按需创建放业务因子
      └── .backup/                    ← _save_backup 写入

    注意:_stub_scan 仍只扫 llm_generated/,业务因子需要用 _register_business_factor
    helper（下面）手工注入 registry。
    """
    factors_root = isolated_llm_dir.parent
    monkeypatch.setattr(factors_router, "_FACTORS_ROOT", factors_root.resolve())
    return factors_root


def _register_business_factor(factors_root: Path, category: str, factor_id: str) -> Path:
    """在 factors_root/<category>/<factor_id>.py 落盘一个 BaseFactor 子类,并手工注册到 registry。

    返回源码文件路径。测试方后续可以 PUT /api/factors/{factor_id}/code 触发覆写。
    """
    import hashlib
    import importlib.util
    import inspect as _inspect
    import sys as _sys

    from backend.runtime.factor_registry import FactorRegistry

    target = factors_root / category
    target.mkdir(exist_ok=True)
    f = target / f"{factor_id}.py"
    f.write_text(_CODE_TMPL.format(fid=factor_id).replace('category = "momentum"', f'category = "{category}"'))

    mod_name = f"_test_biz_{category}_{factor_id}"
    spec = importlib.util.spec_from_file_location(mod_name, f)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    cls = next(
        obj
        for _, obj in _inspect.getmembers(mod, _inspect.isclass)
        if getattr(obj, "factor_id", None) == factor_id
    )
    reg = FactorRegistry()
    with reg._lock:
        reg._classes[factor_id] = cls
        reg._code_hash[factor_id] = hashlib.sha1(
            _inspect.getsource(cls).encode()
        ).hexdigest()
        reg._version[factor_id] = 1
    return f


# ---------------------------- PUT 业务因子（放开 llm_generated 边界后） ----------------------------


def test_put_business_factor_succeeds_with_backup(isolated_factors_root):
    """业务因子（momentum/）现在可通过 PUT 覆写,响应 backup_path 非 null。"""
    from fastapi.testclient import TestClient
    from backend.api.main import app

    src = _register_business_factor(isolated_factors_root, "momentum", "biz_momo")
    new_code = _CODE_TMPL.format(fid="biz_momo").replace('"Foo"', '"Changed"')

    with TestClient(app) as c:
        r = c.put("/api/factors/biz_momo/code", json={"code": new_code})

    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["display_name"] == "Changed"
    assert body["backup_path"] is not None
    assert ".backup" in body["backup_path"]
    # 文件真被覆写
    assert '"Changed"' in src.read_text()


def test_put_business_factor_creates_backup_on_disk(isolated_factors_root):
    """备份文件真实落在 .backup/<factor_id>.<ts>.py,内容是旧版本。"""
    from fastapi.testclient import TestClient
    from backend.api.main import app

    src = _register_business_factor(isolated_factors_root, "reversal", "biz_rev")
    original = src.read_text()
    new_code = _CODE_TMPL.format(fid="biz_rev").replace('"Foo"', '"Updated"')

    with TestClient(app) as c:
        r = c.put("/api/factors/biz_rev/code", json={"code": new_code})

    assert r.status_code == 200
    backup_dir = isolated_factors_root / ".backup"
    backups = list(backup_dir.glob("biz_rev.*.py"))
    assert len(backups) == 1
    assert backups[0].read_text() == original  # 备份内容是旧版本,不是新版


def test_put_llm_factor_also_creates_backup(isolated_factors_root):
    """备份对所有 PUT 生效,不只业务因子:llm_generated 再次 PUT 也有备份。"""
    from fastapi.testclient import TestClient
    from backend.api.main import app

    with TestClient(app) as c:
        assert _create_via_api(c, "llm_backup_test").status_code == 200
        new_code = _CODE_TMPL.format(fid="llm_backup_test").replace('"Foo"', '"V2"')
        r = c.put("/api/factors/llm_backup_test/code", json={"code": new_code})

    assert r.status_code == 200
    assert r.json()["data"]["backup_path"] is not None


def test_put_business_factor_keeps_only_5_backups(isolated_factors_root):
    """连续 PUT 同一业务因子 6 次,.backup/ 下只剩 5 份。"""
    import time

    from fastapi.testclient import TestClient
    from backend.api.main import app

    _register_business_factor(isolated_factors_root, "momentum", "biz_5cap")

    with TestClient(app) as c:
        for i in range(6):
            new_code = _CODE_TMPL.format(fid="biz_5cap").replace(
                '"Foo"', f'"V{i + 1}"'
            )
            r = c.put("/api/factors/biz_5cap/code", json={"code": new_code})
            assert r.status_code == 200
            time.sleep(1.01)  # 确保时间戳递增

    backup_dir = isolated_factors_root / ".backup"
    assert len(list(backup_dir.glob("biz_5cap.*.py"))) == 5


def test_delete_business_factor_still_403(isolated_factors_root):
    """删除边界不变:业务因子 DELETE 仍 403。"""
    from fastapi.testclient import TestClient
    from backend.api.main import app

    _register_business_factor(isolated_factors_root, "momentum", "biz_del")

    with TestClient(app) as c:
        r = c.delete("/api/factors/biz_del")

    assert r.status_code == 403
