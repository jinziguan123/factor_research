# 因子源码只读/编辑切换 + 自动备份 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让所有因子（业务目录 + llm_generated）都能从前端查看与编辑源码，PUT 前自动备份到 `.backup/` 保留最近 5 份；业务因子编辑态红色警示；删除仍仅限 llm_generated。

**Architecture:**
- 后端：拆掉 `PUT /api/factors/{id}/code` 的 llm_generated 403 边界，改用更宽的 `_require_factor_file`（仅要求路径落在 `backend/factors/**/*.py`）。新增 `_save_backup` helper 在覆写前拷贝旧文件到 `backend/factors/.backup/`。
- 前端：`FactorDetail.vue` 中「编辑源码」按钮改为「源码」无条件显示；modal 内 `readonly` ref 切换查看/编辑态；基于 `factor.editable` 分级显示黄/红 alert；保存 toast 附带备份路径。
- 删除按钮保持原逻辑（仅 llm_generated 可删），本次不动。

**Tech Stack:** FastAPI + pathlib / shutil / datetime；Vue 3 + Naive UI + vue-query；pytest + TestClient + monkeypatch 隔离

**Design doc**: `docs/plans/2026-04-22-factor-source-edit-toggle-design.md`（commit `90bb698`）

---

## 前置：启动隔离分支（仅首次执行时）

当前在 master。建议在本 worktree 上启动一个 feature 分支再动手：

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research
git status                          # 确认工作树干净
git checkout -b feat/factor-source-edit-toggle
git log --oneline -1                # 起点应为 90bb698 (设计稿)
```

所有 Task 的 commit 都落在此分支，完成后由用户决定 merge 策略（见 @superpowers:finishing-a-development-branch）。

---

## Task 1: 后端 `_FACTORS_ROOT` + `_require_factor_file` helper

**Files:**
- Modify: `backend/api/routers/factors.py`
- Test: `backend/tests/test_api_factor_crud.py`（扩展）

### Step 1: 读现有代码定位改动点

Run: `Read backend/api/routers/factors.py:40-82`

观察目标：
- `_LLM_DIR` 定义在 line 42
- `_is_under_llm_dir` / `_factor_source_file` / `_require_llm_file` 这三个 helper 都在 line 48-82
- 新函数 `_require_factor_file` 要放在 `_require_llm_file` 之后，保持分组

### Step 2: 写失败的单元测试

**Append to** `backend/tests/test_api_factor_crud.py`（放在"`_is_under_llm_dir`"测试块之后，"`_verify_class_factor_id`"块之前，新开一个"`_require_factor_file`"分节）：

```python
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
```

### Step 3: 运行测试，确认失败

Run (from `backend/` directory): 
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -k "require_factor_file" -v
```
Expected: FAIL with `AttributeError: module ... has no attribute '_require_factor_file'`

### Step 4: 实现 `_FACTORS_ROOT` 常量 + `_require_factor_file` helper

**Modify** `backend/api/routers/factors.py:40-42`（在 `_LLM_DIR` 后一行追加）：

```python
# llm_generated 目录的绝对路径；计算方式与 factor_assistant._LLM_FACTORS_DIR 对齐
# （后者在 services/,本文件在 api/routers/,两者都 parent.parent 到 backend/ 再往下）。
_LLM_DIR = (Path(__file__).resolve().parent.parent.parent / "factors" / "llm_generated").resolve()

# backend/factors/ 的绝对路径。用于放开业务目录（momentum/reversal/oscillator/...）
# 的 PUT 编辑权限:路径校验改成"必须在 _FACTORS_ROOT 下的 .py",不再限死 llm_generated。
# DELETE 仍用 _LLM_DIR + _require_llm_file,保持原有的沙盒删除语义。
_FACTORS_ROOT = (Path(__file__).resolve().parent.parent.parent / "factors").resolve()
```

**Modify** `backend/api/routers/factors.py`（在 `_require_llm_file` 定义之后,line 82 后追加）：

```python
def _require_factor_file(factor_id: str, reg: FactorRegistry) -> Path:
    """拿到源码文件路径并强制要求位于 backend/factors/ 下的 .py 文件;否则 4xx。

    与 _require_llm_file 的区别:允许业务目录（momentum/reversal/oscillator/...）下的
    因子通过校验。用于 PUT /api/factors/{id}/code 的放开路径;DELETE 不走这条,仍用
    _require_llm_file 保持沙盒删除语义。

    只用 is_relative_to(_FACTORS_ROOT) 做路径白名单,防止 inspect.getsourcefile 返回
    一个位于 site-packages / 系统路径 / 符号链接到外面的文件被接受。
    """
    p = _factor_source_file(factor_id, reg)
    if not p.is_relative_to(_FACTORS_ROOT):
        raise HTTPException(
            status_code=403,
            detail=f"源码文件必须位于 backend/factors/ 下,实际 {p}",
        )
    if p.suffix != ".py":
        raise HTTPException(
            status_code=400,
            detail=f"源码文件必须是 .py,实际后缀 {p.suffix!r}",
        )
    return p
```

### Step 5: 运行测试,确认通过

Run (from `backend/`):
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -k "require_factor_file" -v
```
Expected: 4 passed

### Step 6: 跑全量后端测试确认无回归

Run:
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -v
```
Expected: 全部 passed（原有 + 新 4 个）

### Step 7: Commit

```bash
git add backend/api/routers/factors.py backend/tests/test_api_factor_crud.py
git commit -m "$(cat <<'EOF'
feat(api/factors): 新增 _FACTORS_ROOT + _require_factor_file helper

为后续放开业务因子的 PUT 编辑权限做准备:
- 新常量 _FACTORS_ROOT 指向 backend/factors/ 绝对路径
- 新 helper _require_factor_file 仅要求路径在该目录下的 .py,不限死 llm_generated
- DELETE 仍用旧的 _require_llm_file,不变

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 后端 `_save_backup` helper

**Files:**
- Modify: `backend/api/routers/factors.py`
- Test: `backend/tests/test_api_factor_crud.py`（扩展）

### Step 1: 写失败的单元测试

**Append to** `backend/tests/test_api_factor_crud.py` 在 `_require_factor_file` 块之后,`_verify_class_factor_id` 块之前开新分节:

```python
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
```

### Step 2: 运行测试,确认失败

Run:
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -k "save_backup" -v
```
Expected: FAIL with `AttributeError: ... has no attribute '_save_backup'`

### Step 3: 实现 `_save_backup` helper

**Modify** `backend/api/routers/factors.py`:

(a) **顶部 import 区补两行**（在现有 `import ast` 等后）：

```python
import shutil
from datetime import datetime
```

(b) 在 `_require_factor_file` 之后、`_verify_class_factor_id` 之前追加:

```python
def _save_backup(p: Path, factor_id: str) -> Path | None:
    """覆写前把旧文件拷贝到 .backup/<factor_id>.<yyyyMMdd-HHmmss>.py。

    - 旧文件不存在 → 返回 None、不创建 .backup 目录（新因子首次 PUT 的防御分支）
    - 对每个 factor_id 只保留最近 5 份,多余按文件名字典序删除（yyyyMMdd-HHmmss
      格式保证字典序 = 时间序）
    - copy2 保留 mtime,便于外部工具按修改时间排序

    备份范围:对所有 PUT 的因子都做（含 llm_generated）,保守一致。
    """
    if not p.exists():
        return None
    backup_dir = _FACTORS_ROOT / ".backup"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{factor_id}.{ts}.py"
    shutil.copy2(p, dest)
    # 只保留最近 5 份,旧的按字典序删除
    olds = sorted(backup_dir.glob(f"{factor_id}.*.py"))
    for stale in olds[:-5]:
        stale.unlink(missing_ok=True)
    return dest
```

### Step 4: 运行测试,确认通过

Run:
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -k "save_backup" -v
```
Expected: 4 passed（注意 `test_save_backup_keeps_only_5_latest` 和 `test_save_backup_different_factors_dont_interfere` 各含 sleep,单测耗时 ~6+3=9 秒）

### Step 5: Commit

```bash
git add backend/api/routers/factors.py backend/tests/test_api_factor_crud.py
git commit -m "$(cat <<'EOF'
feat(api/factors): 新增 _save_backup helper

PUT 覆写前把旧文件拷到 backend/factors/.backup/<factor_id>.<timestamp>.py,
每个 factor_id 保留最近 5 份。目录不存在时懒创建,源文件不存在时返回 None。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 后端 PUT 集成新 helper + backup_path 响应字段

**Files:**
- Modify: `backend/api/routers/factors.py:210-245`（`update_factor_code` 函数）
- Test: `backend/tests/test_api_factor_crud.py`（扩展端到端测试 + 改原回归测试）

### Step 1: 扩展 fixture 支持业务因子

**Modify** `backend/tests/test_api_factor_crud.py`,在 `isolated_llm_dir` fixture 之后追加新 fixture:

```python
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
```

### Step 2: 写失败的端到端测试

**Append to** `backend/tests/test_api_factor_crud.py`（文件末尾,现有 `test_put_and_delete_reject_non_llm_generated` 之后）:

```python
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
```

### Step 3: 改动原 `test_put_and_delete_reject_non_llm_generated`

该测试原断言 PUT 和 DELETE 对非 llm_generated 都 403。现在 PUT 边界拆了,只剩 DELETE 还 403。**改成**只测 DELETE:

**Modify** `backend/tests/test_api_factor_crud.py`,替换 `test_put_and_delete_reject_non_llm_generated` 为:

```python
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
```

### Step 4: 运行全部新/改测试,确认失败

Run:
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -k "business_factor or llm_factor_also or delete_rejects" -v
```
Expected: 6 FAIL——都是因为响应里没有 `backup_path` 字段 + PUT 对业务因子返回 403。

### Step 5: 改 `update_factor_code` 实现

**Modify** `backend/api/routers/factors.py:210-245`,把 `update_factor_code` 替换成:

```python
@router.put("/{factor_id}/code")
def update_factor_code(factor_id: str, body: UpdateFactorCodeIn) -> dict:
    """覆写因子源码。

    放开的权限边界:允许 backend/factors/ 下所有 .py 被 PUT 覆写（含业务目录
    momentum/reversal/oscillator/... 以及 llm_generated/）。DELETE 仍仅限
    llm_generated,保持沙盒删除语义。

    流程:locate 文件 → 路径白名单校验（_require_factor_file）→ AST 白名单校验 →
    类属性一致性校验 → 备份旧文件到 .backup/ → 落盘 → reload_module + reset_pool
    → 回读元数据 + 附带 backup_path 返回。
    """
    reg = FactorRegistry()
    reg.scan_and_register()
    p = _require_factor_file(factor_id, reg)

    try:
        fa._validate_code_ast(body.code)
    except fa.FactorAssistantError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _verify_class_factor_id(body.code, factor_id)

    # AST / 一致性校验都通过,此时才备份 → 避免坏代码触发一次无意义的备份
    backup_path = _save_backup(p, factor_id)

    body_text = body.code if body.code.endswith("\n") else body.code + "\n"
    p.write_text(body_text, encoding="utf-8")
    mod = inspect.getmodule(reg.get(factor_id).__class__)
    if mod is not None:
        reg.reload_module(mod.__name__)
    reset_pool()
    inst = reg.get(factor_id)
    logger.info(
        "update_factor_code: factor_id=%s path=%s backup=%s",
        factor_id, p, backup_path,
    )
    return ok(
        {
            "factor_id": inst.factor_id,
            "display_name": inst.display_name,
            "category": inst.category,
            "description": inst.description,
            "version": reg.current_version(factor_id),
            # 新增字段:给前端展示"已备份至 ..." toast;新因子首次 PUT 为 null
            "backup_path": str(backup_path) if backup_path else None,
        }
    )
```

### Step 6: 运行改动后的测试,确认通过

Run:
```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -v
```
Expected: 全部 passed（原有 + 新 6 + 改 1）

### Step 7: Commit

```bash
git add backend/api/routers/factors.py backend/tests/test_api_factor_crud.py
git commit -m "$(cat <<'EOF'
feat(api/factors): PUT 放开业务因子编辑 + backup_path 响应字段

- PUT /api/factors/{id}/code 从 _require_llm_file 改调 _require_factor_file,
  业务目录（momentum/reversal/oscillator/...）的因子现在可以覆写
- 覆写前调 _save_backup 把旧文件拷到 backend/factors/.backup/,响应新增
  backup_path 字段（新因子首次 PUT 为 null）
- DELETE 边界不变,仍仅 llm_generated 可删;原 test_put_and_delete_reject_non_llm_generated
  拆成只验 DELETE 的 test_delete_rejects_non_llm_generated
- 新增端到端测试覆盖业务因子 PUT、备份创建、5 份保留上限等场景

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `.gitignore` 追加备份目录

**Files:**
- Modify: `.gitignore`

### Step 1: 查看现状

Run:
```bash
Read .gitignore
```

确认没有 `backend/factors/.backup/` 条目。

### Step 2: 追加条目

**Edit** `.gitignore`,在文件末尾追加:

```
# 因子源码前端编辑时 PUT 覆写前的自动备份,保留最近 5 份/因子
backend/factors/.backup/
```

### Step 3: 验证

Run:
```bash
git check-ignore -v backend/factors/.backup/some.py
```
Expected: 输出 `.gitignore:<line>:backend/factors/.backup/	backend/factors/.backup/some.py`（表示被忽略）

### Step 4: Commit

```bash
git add .gitignore
git commit -m "$(cat <<'EOF'
chore: .gitignore 追加 backend/factors/.backup/

因子源码前端编辑时 PUT 覆写前的自动备份目录,每因子保留最近 5 份;
属运行时产物,不应提交。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 前端 TS 类型追加 `backup_path`

**Files:**
- Modify: `frontend/src/api/factors.ts:31-37`

### Step 1: 改 `FactorMutationResult`

**Modify** `frontend/src/api/factors.ts:31-37`,把 interface 改为:

```typescript
export interface FactorMutationResult {
  factor_id: string
  display_name: string
  category: string
  description: string
  version: number
  /**
   * PUT /api/factors/{id}/code 成功时,返回覆写前的备份路径（相对 repo 根）。
   * 新因子首次 PUT（文件原本不存在）为 null。POST 新建因子不返回此字段。
   */
  backup_path?: string | null
}
```

也顺手更新注释文案(line 6-7)：

```typescript
// 读:list / detail / source code
// 写:PUT 覆写源码 / DELETE 删因子 / POST 空白模板新建（不经 LLM）
//
// 查看范围:所有因子均可读取源码。
// 编辑范围:所有因子均可 PUT 覆写源码（业务因子覆写会修改 git working tree,
// 前端按 factor.editable 字段分级显示黄色/红色警示）。后端覆写前自动备份到
// backend/factors/.backup/,响应里 backup_path 字段告知前端展示。
// 删除范围:仍仅限 backend/factors/llm_generated/ 下的因子,业务因子 DELETE 返回 403。
```

### Step 2: 类型检查

Run:
```bash
cd frontend && pnpm type-check
```
Expected: 0 errors（或使用项目自带的命令,可能是 `npm run type-check` / `yarn tsc --noEmit`,跟项目习惯走）

### Step 3: Commit

```bash
git add frontend/src/api/factors.ts
git commit -m "$(cat <<'EOF'
feat(frontend/api): FactorMutationResult 追加 backup_path 字段

PUT /api/factors/{id}/code 成功响应携带备份路径,前端 toast 展示
"已备份至 ..." 给用户一个可见的后悔药入口。
顺带更新模块注释说明新的查看/编辑/删除边界。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 前端 FactorDetail.vue 按钮 + 状态机

**Files:**
- Modify: `frontend/src/pages/factors/FactorDetail.vue`

### Step 1: 改顶部按钮组

**Modify** `frontend/src/pages/factors/FactorDetail.vue:146-166`,把按钮组替换成:

```vue
      <template #extra>
        <n-space>
          <n-button
            type="primary"
            @click="router.push(`/evals/new?factor_id=${factorId}`)"
          >
            新评估
          </n-button>
          <n-button
            secondary
            @click="router.push(`/backtests/new?factor_id=${factorId}`)"
          >
            新回测
          </n-button>
          <!-- 源码按钮:所有因子可见,弹窗内部默认只读,用户点"编辑"才切到可写态 -->
          <n-button secondary @click="openSource">源码</n-button>
          <n-button v-if="factor?.editable" type="error" secondary @click="confirmDelete">
            删除
          </n-button>
        </n-space>
      </template>
```

关键:
- 旧的 `v-if="factor?.editable"` 那个"编辑源码"按钮**删掉**
- 新增"源码"按钮,无条件显示
- 删除按钮的 `v-if="factor?.editable"` 条件**保留**

### Step 2: 改 script setup 部分的状态逻辑

**Modify** `frontend/src/pages/factors/FactorDetail.vue:60-104`,把"编辑源码对话框"那一大段替换成:

```typescript
// ---------------- 源码查看/编辑对话框 ----------------
// 打开后默认 ReadOnly 态;点"编辑"切到 Editing 态;保存成功后自动回到 ReadOnly。
// 业务因子（editable=false）进入 Editing 态时显示红色强警示;llm_generated 黄色。
const sourceOpen = ref(false)
const editing = ref(false)      // false=ReadOnly, true=Editing
const editCode = ref('')
const editError = ref('')
const originalCode = ref('')    // 进 Editing 态时的快照,用于"放弃修改"对比

const { data: factorCode, isFetching: codeLoading } = useFactorCode(
  factorId,
  sourceOpen,
)

// 后端返回新源码时同步到文本域;同时重置 originalCode 快照。
watch(factorCode, (v) => {
  if (v && sourceOpen.value) {
    editCode.value = v.code
    originalCode.value = v.code
  }
})

function openSource() {
  editError.value = ''
  editing.value = false  // 默认只读
  // 用缓存立即填充;watch(factorCode) 刷新后再覆盖
  // （cache 可能过期,但先给个值避免空窗期）
  editCode.value = factorCode.value?.code ?? ''
  originalCode.value = factorCode.value?.code ?? ''
  sourceOpen.value = true
}

function enterEditing() {
  editError.value = ''
  // 进 Editing 态时把当前 code 存为快照,供"放弃修改"对比
  originalCode.value = editCode.value
  editing.value = true
}

function cancelEditing() {
  const dirty = editCode.value !== originalCode.value
  if (!dirty) {
    editing.value = false
    return
  }
  dialog.warning({
    title: '放弃未保存的修改？',
    content: '编辑器里有未保存的改动,切回查看态会丢失。',
    positiveText: '放弃修改',
    negativeText: '继续编辑',
    onPositiveClick: () => {
      editCode.value = originalCode.value  // 回滚
      editing.value = false
    },
  })
}

const { mutateAsync: updateCode, isPending: savePending } = useUpdateFactorCode()

async function saveEdit() {
  editError.value = ''
  const code = editCode.value
  if (code.trim().length < 10) {
    editError.value = '源码过短（至少 10 字符）,请检查是否清空了编辑器'
    return
  }
  try {
    const res = await updateCode({ factor_id: factorId.value, code })
    const msg = res.backup_path
      ? `保存成功:${res.display_name}（v${res.version}）\n已备份至 ${res.backup_path}`
      : `保存成功:${res.display_name}（v${res.version}）`
    message.success(msg)
    // 保存成功后:刷新本地 code 快照,切回 ReadOnly 态
    originalCode.value = code
    editing.value = false
  } catch (e: any) {
    editError.value =
      e?.response?.data?.message ??
      e?.response?.data?.detail ??
      e?.message ??
      '保存失败'
  }
}
```

**IMPORTANT:** 把顶部 import 里的 `useUpdateFactorCode, useDeleteFactor` 保留,把 `editOpen` 全局替换为 `sourceOpen`(只 ref 那一处,其他变量名不变)。

### Step 3: 改 modal 模板

**Modify** `frontend/src/pages/factors/FactorDetail.vue:200-242`（`<n-modal>` 段）:

```vue
    <!-- 源码查看/编辑弹窗 -->
    <n-modal
      v-model:show="sourceOpen"
      preset="card"
      :title="editing ? `编辑源码:${factor?.display_name ?? factorId}` : `查看源码:${factor?.display_name ?? factorId}`"
      style="width: 960px; max-width: 95vw"
      :mask-closable="!savePending"
      :close-on-esc="!savePending"
    >
      <!-- Editing 态警示:按 factor.editable 分级 -->
      <n-alert
        v-if="editing && factor?.editable"
        type="warning"
        :show-icon="false"
        style="margin-bottom: 12px"
      >
        直接覆写 <code>backend/factors/llm_generated/{{ factorId }}.py</code>。
        保存前后端做 AST 白名单校验 + 类属性 <code>factor_id</code> 必须等于
        <code>{{ factorId }}</code>;保存成功后自动备份旧版本到
        <code>.backup/</code>（保留最近 5 份）、热加载生效。
      </n-alert>

      <n-alert
        v-else-if="editing && factor && !factor.editable"
        type="error"
        :show-icon="false"
        style="margin-bottom: 12px"
      >
        ⚠️ 这是业务因子（位于 <code>backend/factors/{{ factor.category }}/{{ factorId }}.py</code>）,
        保存会直接覆写 git working tree 里的源码文件。
        <br />
        建议先 <code>git commit</code> 当前状态再修改,以便出错时能用
        <code>git checkout</code> 回滚。后端会在覆写前自动备份到
        <code>.backup/</code>（保留最近 5 份）,但这只是手滑兜底,不是正式版本管理手段。
      </n-alert>

      <n-spin :show="codeLoading">
        <py-code-editor
          v-model="editCode"
          :readonly="!editing"
          :disabled="savePending"
          height="520px"
          placeholder="加载中..."
        />
      </n-spin>

      <n-alert v-if="editError" type="error" :show-icon="false" style="margin-top: 8px">
        {{ editError }}
      </n-alert>

      <template #action>
        <n-space justify="end">
          <!-- ReadOnly 态:[关闭] [编辑] -->
          <template v-if="!editing">
            <n-button @click="sourceOpen = false">关闭</n-button>
            <n-button type="primary" :disabled="codeLoading" @click="enterEditing">
              编辑
            </n-button>
          </template>
          <!-- Editing 态:[取消编辑] [保存] -->
          <template v-else>
            <n-button :disabled="savePending" @click="cancelEditing">
              取消编辑
            </n-button>
            <n-button
              type="primary"
              :loading="savePending"
              :disabled="codeLoading"
              @click="saveEdit"
            >
              {{ savePending ? '保存中…' : '保存' }}
            </n-button>
          </template>
        </n-space>
      </template>
    </n-modal>
```

### Step 4: 确认 PyCodeEditor 支持 `readonly` prop

Run:
```bash
Read frontend/src/components/forms/PyCodeEditor.vue
```

检查点:
- defineProps 里是否有 `readonly?: boolean`
- 如果**没有**,先在 PyCodeEditor 里加这个 prop,转发给 CodeMirror 的 `readonly: EditorState.readOnly.of(props.readonly ?? false)` 或对应配置

如果 PyCodeEditor 已经支持(AI 生成结果预览那里用了 `readonly`,line 440 `<py-code-editor :model-value="aiResult.code" readonly ...>`),跳过这一步。

### Step 5: 手测 - 业务因子查看

1. `cd backend && uv run uvicorn backend.api.main:app --reload --port 8000`
2. `cd frontend && pnpm dev`
3. 浏览器打开 `http://localhost:5173/factors/kdj_cross`
4. 应看到右上角「源码」按钮,不应看到「删除」按钮
5. 点「源码」→ 弹窗默认 ReadOnly,标题"查看源码:KDJ 金叉强度",底部按钮"关闭 / 编辑"
6. 点「编辑」→ 标题变为"编辑源码:...",顶部出现红色 alert,编辑器可写,底部变"取消编辑 / 保存"
7. 不改任何东西点「取消编辑」→ 直接回到 ReadOnly 态（无确认 dialog）
8. 再进 Editing 态,随便改一行 → 点「取消编辑」→ 弹出二次确认 dialog
9. 点「放弃修改」→ 编辑器内容回滚、切回 ReadOnly

### Step 6: 手测 - llm_generated 因子

找一个 llm_generated 下的因子(如果没有,`/factors` 页点"从模板新建"随便造一个)。

1. 进详情页,右上应同时看到「源码」和「删除」
2. 点「源码」→ 同上流程
3. 进 Editing 态 → 应显示**黄色** alert,文案是 llm_generated 版本

### Step 7: Commit

```bash
git add frontend/src/pages/factors/FactorDetail.vue
git commit -m "$(cat <<'EOF'
feat(frontend/factors): 源码只读/编辑切换 + 分级警示

- 「编辑源码」按钮替换为无条件显示的「源码」按钮;打开弹窗默认只读
- Editing 态按 factor.editable 分级:llm_generated 黄色 alert,业务因子红色
- 取消编辑时若有未保存变更,弹出二次确认 dialog,防止误关丢失
- 保存成功后自动切回 ReadOnly 态,toast 带出 backup_path

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 验证 PyCodeEditor `readonly` prop（防御性确认）

**仅在 Task 6 Step 4 发现 PyCodeEditor 不支持 readonly 时执行**。如果已支持,跳过本 Task。

**Files:**
- Modify: `frontend/src/components/forms/PyCodeEditor.vue`

### Step 1: 读组件看当前 prop

Run:
```bash
Read frontend/src/components/forms/PyCodeEditor.vue
```

### Step 2: 加 readonly prop

按 CodeMirror 的 API 加。典型实现(具体代码按组件现状适配):

```typescript
const props = defineProps<{
  modelValue: string
  height?: string
  placeholder?: string
  disabled?: boolean
  readonly?: boolean   // 新增
}>()

// 在 EditorState.create 的 extensions 里加:
// EditorState.readOnly.of(props.readonly ?? false)
// 或者用 watch(() => props.readonly, newVal => view.dispatch(...)) 动态切换
```

### Step 3: 手测

回到 Task 6 Step 5、6,重新验证 ReadOnly 态编辑器确实不可写。

### Step 4: Commit

```bash
git add frontend/src/components/forms/PyCodeEditor.vue
git commit -m "$(cat <<'EOF'
feat(frontend/forms): PyCodeEditor 新增 readonly prop

用于因子源码查看态禁用编辑。传 readonly=true 时 CodeMirror 进入只读模式,
键盘输入和粘贴均被禁用。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## 最终验证

### 后端全量回归

```bash
cd backend && uv run pytest tests/test_api_factor_crud.py -v
```
Expected: 全部 passed（原 15 个 + 新 14 个,共 ~29 个）

### 前端类型检查 + 构建

```bash
cd frontend && pnpm type-check && pnpm build
```
Expected: 0 errors, build 成功

### 手测场景清单（设计稿 §7.3）

- [ ] 查看业务因子（KDJ/momentum/reversal/oscillator）源码 → readonly 编辑器正常显示
- [ ] 编辑业务因子 → 红色 alert 显示 → 保存 → toast 显示备份路径 → `.backup/` 下文件存在
- [ ] 编辑 llm_generated 因子 → 黄色 alert → 保存成功
- [ ] 连续编辑同一业务因子 6 次 → `.backup/` 下只剩 5 份
- [ ] 编辑态未保存时点「取消编辑」→ 弹出二次确认 dialog
- [ ] 通过 API 直接 `DELETE /api/factors/<业务因子>` → 仍返回 403
- [ ] 保存失败(故意写坏语法)→ 红色 error alert,编辑器停留在编辑态

---

## 完成后

参考 @superpowers:finishing-a-development-branch 决定 merge 策略。

此时设计稿和实施 commits 应为:
- `90bb698` docs(plans): 新增因子源码只读/编辑切换 设计文档
- Task 1-7 各自一个 commit

共 8 个 commits(含设计稿)。

## 附录:所有 commit 预期命名规范

| Task | commit message 开头 |
|---|---|
| 1 | `feat(api/factors): 新增 _FACTORS_ROOT + _require_factor_file helper` |
| 2 | `feat(api/factors): 新增 _save_backup helper` |
| 3 | `feat(api/factors): PUT 放开业务因子编辑 + backup_path 响应字段` |
| 4 | `chore: .gitignore 追加 backend/factors/.backup/` |
| 5 | `feat(frontend/api): FactorMutationResult 追加 backup_path 字段` |
| 6 | `feat(frontend/factors): 源码只读/编辑切换 + 分级警示` |
| 7 | `feat(frontend/forms): PyCodeEditor 新增 readonly prop`（仅必要时） |
