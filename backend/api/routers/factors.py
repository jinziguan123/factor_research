"""因子目录（CRUD 只读 + reload）。

- ``GET /api/factors``：列所有已注册因子；调用前先 ``scan_and_register`` 兜底初始扫描，
  防止部分测试场景 startup 未触发（例如不使用 ``with TestClient`` 的 health 型测试）；
  实现在 registry 内是幂等的，不会重复 bump version。
- ``GET /api/factors/{factor_id}``：返回单个因子详情；未注册走 404。
- ``POST /api/factors/reload``：重扫因子目录 + 重置 worker 进程池，把 worker 里
  的陈旧字节码也冲掉——与前端"手动刷新"按钮配合。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import reset_pool

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("")
def list_factors() -> dict:
    """列出所有已注册因子。"""
    reg = FactorRegistry()
    # 保证即便 startup 未跑，也能返回一份一致的因子清单（例如某些集成测试不开 lifespan）。
    reg.scan_and_register()
    return ok(reg.list())


@router.get("/{factor_id}")
def get_factor(factor_id: str) -> dict:
    """返回单个因子的详细元数据（含 params_schema、当前 version）。"""
    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        inst = reg.get(factor_id)
    except KeyError:
        # 交给全局异常 handler 统一转成 ``{"code":404, "message":...}``。
        raise HTTPException(status_code=404, detail="factor not found")
    return ok(
        {
            "factor_id": inst.factor_id,
            "display_name": inst.display_name,
            "category": inst.category,
            "description": inst.description,
            "params_schema": inst.params_schema,
            "default_params": inst.default_params,
            "supported_freqs": list(inst.supported_freqs),
            "version": reg.current_version(factor_id),
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
    # 只要用户显式点了 reload，就重建池。代价是让正在途中的任务失去后续 submit 资格
    # （但不会被取消），换得"立即生效"的确定性。
    reset_pool()
    return ok({"updated": updated})
