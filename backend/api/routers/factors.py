"""因子目录 CRUD。

- ``GET /api/factors``：列所有已注册因子；调用前先 ``scan_and_register`` 兜底初始扫描，
  防止部分测试场景 startup 未触发（例如不使用 ``with TestClient`` 的 health 型测试）；
  实现在 registry 内是幂等的，不会重复 bump version。
- ``GET /api/factors/{factor_id}``：返回单个因子详情；未注册走 404。多带一个
  ``editable: bool``——源码位于 ``backend/factors/llm_generated/`` 下为 True，
  前端据此决定是否展示"编辑源码 / 删除"按钮。
- ``GET /api/factors/{factor_id}/code``：返回源码文本（所有因子可读）。
- ``PUT /api/factors/{factor_id}/code``：覆写源码；允许 ``backend/factors/`` 下所有因子
  （含业务目录 momentum / reversal / oscillator / ... 以及 llm_generated/）。
  过 AST 白名单 + 类 factor_id 一致性校验，覆写前自动备份旧文件到 ``.backup/``
  （每个 factor_id 保留最近 5 份），落盘后强制 reload + 重置 worker 进程池。
- ``DELETE /api/factors/{factor_id}``：删源码文件 + 注册表摘除；**仍仅允许 llm_generated**。
- ``POST /api/factors``：从源码创建新因子，落盘到 ``llm_generated/<factor_id>.py``。
  与 ``factor_assistant`` 复用 AST 校验 / 落盘逻辑，区别是源码由用户直接提供（不走 LLM）。
- ``POST /api/factors/reload``：重扫因子目录 + 重置 worker 进程池。

**安全边界**：PUT 允许 ``backend/factors/`` 下所有 .py 被覆写（业务因子前端会显示红色
警示，提醒这是手写资产），写前自动备份；DELETE / POST 仍仅限 ``llm_generated/``，
避免误删 / 误建业务代码资产。路径校验通过 ``Path.resolve().relative_to()`` 完成，
不信任任何用户输入的路径片段。
"""
from __future__ import annotations

import ast
import inspect
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.schemas import ok
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import reset_pool
from backend.services import factor_assistant as fa

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/factors", tags=["factors"])

# llm_generated 目录的绝对路径；计算方式与 factor_assistant._LLM_FACTORS_DIR 对齐
# （后者在 services/，本文件在 api/routers/，两者都 parent.parent 到 backend/ 再往下）。
_LLM_DIR = (Path(__file__).resolve().parent.parent.parent / "factors" / "llm_generated").resolve()

# backend/factors/ 的绝对路径。用于放开业务目录（momentum/reversal/oscillator/...）
# 的 PUT 编辑权限：路径校验改成"必须在 _FACTORS_ROOT 下的 .py"，不再限死 llm_generated。
# DELETE 仍用 _LLM_DIR + _require_llm_file，保持原有的沙盒删除语义。
_FACTORS_ROOT = (Path(__file__).resolve().parent.parent.parent / "factors").resolve()


# ---------------------------- 辅助 ----------------------------


def _is_under_llm_dir(p: Path) -> bool:
    """判断路径 ``p`` 是否位于 ``backend/factors/llm_generated/`` 下。

    用 ``Path.resolve().relative_to()`` 防 symlink / ``..`` 穿越；不用字符串 startswith
    比较（会被 ``/backend/factors/llm_generated_evil/x.py`` 这类前缀匹配绕过）。
    """
    try:
        p.resolve().relative_to(_LLM_DIR)
        return True
    except ValueError:
        return False


def _factor_source_file(factor_id: str, reg: FactorRegistry) -> Path:
    """定位 factor_id 的源码文件；未注册 → 404，定位失败 → 500。"""
    try:
        inst = reg.get(factor_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="factor not found") from e
    src = inspect.getsourcefile(inst.__class__)
    if not src:
        # inspect 对动态生成的类会返回 None；理论上扫描注册过的都是文件里定义的类。
        raise HTTPException(status_code=500, detail=f"无法定位 {factor_id!r} 的源码文件")
    return Path(src).resolve()


def _require_llm_file(factor_id: str, reg: FactorRegistry) -> Path:
    """拿到源码文件路径并强制要求位于 llm_generated/ 下；否则 403。"""
    p = _factor_source_file(factor_id, reg)
    if not _is_under_llm_dir(p):
        raise HTTPException(
            status_code=403,
            detail=f"该因子位于 {p.parent.name}/，不是 llm_generated/，禁止通过 API 修改或删除",
        )
    return p


def _require_factor_file(factor_id: str, reg: FactorRegistry) -> Path:
    """拿到源码文件路径并强制要求位于 backend/factors/ 下的 .py 文件；否则 4xx。

    与 ``_require_llm_file`` 的区别：允许业务目录（momentum/reversal/oscillator/...）下的
    因子通过校验。用于 PUT /api/factors/{id}/code 的放开路径；DELETE 不走这条，仍用
    ``_require_llm_file`` 保持沙盒删除语义。

    只用 ``is_relative_to(_FACTORS_ROOT)`` 做路径白名单，防止 ``inspect.getsourcefile``
    返回一个位于 site-packages / 系统路径 / 符号链接到外面的文件被接受。
    """
    p = _factor_source_file(factor_id, reg)
    # 显式 resolve 一次,避免依赖 _factor_source_file 内部的 resolve 语义（是幂等操作,
    # 已解析的路径再 resolve 仍是自身）。独立调用时可读性更高、后续重构更安全。
    p = p.resolve()
    if not p.is_relative_to(_FACTORS_ROOT):
        raise HTTPException(
            status_code=403,
            detail=f"源码文件必须位于 backend/factors/ 下，实际 {p}",
        )
    if p.suffix != ".py":
        raise HTTPException(
            status_code=400,
            detail=f"源码文件必须是 .py，实际后缀 {p.suffix!r}",
        )
    return p


def _save_backup(p: Path, factor_id: str) -> Path | None:
    """覆写前把旧文件拷贝到 .backup/<factor_id>.<yyyyMMdd-HHmmss>.py。

    - 旧文件不存在 → 返回 None、不创建 .backup 目录（新因子首次 PUT 的防御分支）
    - 对每个 factor_id 只保留最近 5 份,多余按文件名字典序删除（yyyyMMdd-HHmmss
      格式保证字典序 = 时间序）
    - copy2 保留 mtime,便于外部工具按修改时间排序

    备份范围:对所有 PUT 的因子都做（含 llm_generated）,保守一致。

    返回:新建备份的绝对 Path(供调用者返回给 API 响应做用户提示);
         源不存在时返回 None,调用者应把 None 原样传给前端(backup_path = null)。
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
    pruned = olds[:-5]
    for stale in pruned:
        stale.unlink(missing_ok=True)
    if pruned:
        logger.debug(
            "_save_backup pruned %d stale backup(s) for %s: %s",
            len(pruned), factor_id, [s.name for s in pruned],
        )
    return dest


def _verify_class_factor_id(code: str, expected: str) -> None:
    """解析 AST，确认代码里至少有一个 ``class X(BaseFactor)`` 且其 ``factor_id`` 属性等于 ``expected``。

    动机：
    - PUT 时如果用户在编辑器里把 ``factor_id = "foo"`` 改成 ``"bar"``，但 URL 还是 foo，
      落盘后 scan 会注册出 bar 而保留旧 foo，造成"改名成功但前端看不到"的诡异状态；
    - POST 时如果用户传的 ``factor_id`` 和代码里的类属性不一致，``_save_factor_file`` 会按
      URL 里的 id 命名文件，但 scan 又按类属性注册，文件名与 factor_id 错位难排查。

    所以在写盘前强制一致。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise HTTPException(
            status_code=400, detail=f"代码语法错误：{e.msg}"
        ) from e
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # 只看顶层继承里带 BaseFactor 的类
        has_base = any(
            (isinstance(b, ast.Name) and b.id == "BaseFactor")
            or (isinstance(b, ast.Attribute) and b.attr == "BaseFactor")
            for b in node.bases
        )
        if not has_base:
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "factor_id":
                    val = stmt.value
                    got = val.value if isinstance(val, ast.Constant) else None
                    if got == expected:
                        return
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"代码里类属性 factor_id={got!r} 与请求中的 {expected!r} 不一致；"
                            f"改名请同时修改 URL / 请求体"
                        ),
                    )
    raise HTTPException(
        status_code=400,
        detail="代码中未找到 `class X(BaseFactor): factor_id = '...'` 顶层赋值",
    )


# ---------------------------- 请求体 ----------------------------


class UpdateFactorCodeIn(BaseModel):
    """PUT /api/factors/{factor_id}/code 的请求体。"""

    code: str = Field(..., min_length=10, max_length=40_000, description="完整 .py 文件内容")


class CreateFactorIn(BaseModel):
    """POST /api/factors 的请求体。"""

    factor_id: str = Field(..., min_length=3, max_length=48, description="snake_case，与代码里类属性一致")
    code: str = Field(..., min_length=10, max_length=40_000, description="完整 .py 文件内容")


# ---------------------------- 只读路由 ----------------------------


@router.get("")
def list_factors() -> dict:
    """列出所有已注册因子。"""
    reg = FactorRegistry()
    reg.scan_and_register()
    return ok(reg.list())


@router.get("/{factor_id}")
def get_factor(factor_id: str) -> dict:
    """返回单个因子的详细元数据（含 params_schema、当前 version、editable）。"""
    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        inst = reg.get(factor_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="factor not found")
    src = inspect.getsourcefile(inst.__class__)
    editable = bool(src) and _is_under_llm_dir(Path(src))
    return ok(
        {
            "factor_id": inst.factor_id,
            "display_name": inst.display_name,
            "category": inst.category,
            "description": inst.description,
            "hypothesis": getattr(inst, "hypothesis", ""),
            "params_schema": inst.params_schema,
            "default_params": inst.default_params,
            "supported_freqs": list(inst.supported_freqs),
            "version": reg.current_version(factor_id),
            "editable": editable,
        }
    )


@router.get("/{factor_id}/code")
def get_factor_code(factor_id: str) -> dict:
    """返回源码文本。所有已注册因子均可读（不限 llm_generated）。"""
    reg = FactorRegistry()
    reg.scan_and_register()
    p = _factor_source_file(factor_id, reg)
    try:
        code = p.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取源码失败：{e}") from e
    return ok(
        {
            "factor_id": factor_id,
            "code": code,
            "editable": _is_under_llm_dir(p),
        }
    )


# ---------------------------- 写路由 ----------------------------


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
            "hypothesis": getattr(inst, "hypothesis", ""),
            "version": reg.current_version(factor_id),
            # 新增字段:给前端展示"已备份至 ..." toast;新因子首次 PUT 为 null
            "backup_path": str(backup_path) if backup_path else None,
        }
    )


@router.delete("/{factor_id}")
def delete_factor(factor_id: str) -> dict:
    """删除 ``llm_generated/<factor_id>.py`` 并从 registry 摘除。

    - 文件位置非 llm_generated/ → 403（不可删）；
    - fr_factor_meta 行保留（软删，is_active=0），历史评估 / 回测记录仍可引用其 version；
    - 删完重置 worker 进程池，避免子进程里还缓存着旧字节码。
    """
    reg = FactorRegistry()
    reg.scan_and_register()
    p = _require_llm_file(factor_id, reg)
    try:
        p.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除文件失败：{e}") from e
    reg.unregister(factor_id)
    reset_pool()
    logger.info("delete_factor: factor_id=%s path=%s", factor_id, p)
    return ok({"deleted": factor_id})


@router.post("")
def create_factor(body: CreateFactorIn) -> dict:
    """从源码创建新因子，落盘到 ``llm_generated/<factor_id>.py``。

    与 ``factor_assistant.translate_and_save`` 的区别：源码直接来自用户输入、不经 LLM，
    因此跳过 JSON 解析 / 字段映射那层，直接走 AST 校验 + 落盘。
    """
    if not fa._FACTOR_ID_RE.match(body.factor_id):
        raise HTTPException(
            status_code=400,
            detail=f"factor_id 不合法：{body.factor_id!r}；应为 3-48 位 snake_case",
        )
    try:
        fa._validate_code_ast(body.code)
    except fa.FactorAssistantError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _verify_class_factor_id(body.code, body.factor_id)

    try:
        saved = fa._save_factor_file(body.factor_id, body.code)
    except fa.FactorAssistantError as e:
        # _save_factor_file 的失败语义就是 "文件已存在"，正好对应 409。
        raise HTTPException(status_code=409, detail=str(e)) from e

    reg = FactorRegistry()
    reg.scan_and_register()
    reset_pool()
    try:
        inst = reg.get(body.factor_id)
    except KeyError:
        # 正常流水线不可达：文件落盘成功 + scan 跑过；真到这里多半是类的 factor_id 属性
        # 与 URL 里的不一致（_verify_class_factor_id 应提前拦下，但留个兜底）。
        raise HTTPException(
            status_code=500,
            detail="落盘成功但注册失败；请检查代码中 factor_id 类属性与请求是否一致",
        )
    logger.info(
        "create_factor: factor_id=%s path=%s", body.factor_id, saved
    )
    return ok(
        {
            "factor_id": inst.factor_id,
            "display_name": inst.display_name,
            "category": inst.category,
            "description": inst.description,
            "hypothesis": getattr(inst, "hypothesis", ""),
            "version": reg.current_version(body.factor_id),
        }
    )


@router.post("/reload")
def reload_factors() -> dict:
    """强制重扫因子目录 + 重置 worker 进程池。

    为什么顺带 ``reset_pool``：热加载只刷新主进程 registry，worker 拿到的仍是旧字节码；
    只有 ``reset_pool`` 后才会在下次 submit 时 fork 出加载新代码的子进程。
    """
    reg = FactorRegistry()
    updated = reg.scan_and_register()
    reset_pool()
    return ok({"updated": updated})
