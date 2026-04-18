"""因子助手（Phase 0）路由：自然语言 → LLM → 生成 + 落盘 BaseFactor 子类。

只提供一个 endpoint：``POST /api/factor_assistant/translate``。

错误映射（全部从 ``FactorAssistantError`` 的 message 字面特征识别——service 层抛
错时已经按"哪一步失败"措辞，router 层保持零业务逻辑，只做 HTTP 语义）：

- 未配置 API key → 503  （部署问题，前端提示管理员）
- LLM 网络层 / 上游 5xx → 502 （上游问题，可让用户重试）
- LLM 输出格式 / 校验失败 → 400 （模型输出烂，建议换措辞重试）
- 文件已存在 → 409 （用户层可操作：换 factor_id 或删旧文件）
- 其它：走全局 500 handler

Phase 0 不做：
- 不触发 FactorRegistry 重扫——留给热加载 watchdog / 前端 reload 按钮；
- 不返回 SSE 进度——单次调用 ≤ 60s，前端 loading spinner 足够。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.schemas import ok
from backend.services.factor_assistant import (
    FactorAssistantError,
    translate_and_save,
)

router = APIRouter(prefix="/api/factor_assistant", tags=["factor_assistant"])


class TranslateIn(BaseModel):
    """``POST /api/factor_assistant/translate`` 请求体。

    ``description`` 走中文自然语言，``hints`` 可留空——预留给 Phase 2 的追问上下文。
    """

    description: str = Field(..., min_length=4, max_length=2000)
    hints: str | None = Field(default=None, max_length=2000)


def _map_error_to_status(err: FactorAssistantError) -> int:
    """把 service 层抛出的错误按 message 关键字映射到 HTTP status。

    service 层的 docstring 里已经列出了"哪种错对应哪种 HTTP 语义"——这里就是那张表的实现。
    用关键字而不是 error code：保持 service 层可独立被 CLI/脚本调用，不耦合 HTTP。
    """
    msg = str(err)
    if "OPENAI_API_KEY" in msg:
        return 503
    if "网络层" in msg or "返回错误状态" in msg or "响应结构异常" in msg:
        return 502
    if "已存在" in msg:
        return 409
    # 剩下全是 LLM 输出校验类（JSON 不合法 / 字段缺失 / AST 校验失败等）
    return 400


@router.post("/translate")
def translate(body: TranslateIn) -> dict:
    """把自然语言描述翻译成 BaseFactor 子类源码并落盘。

    成功返回 ``{code:0, data:{factor_id, display_name, category, description,
    default_params, code, saved_path}}``——``code`` 字段供前端展示，``saved_path``
    让用户知道文件落在哪里；下次 FactorRegistry 扫描（热加载 / 手动刷新）就会自动
    注册该因子，不需要重启服务。
    """
    try:
        gen = translate_and_save(body.description, body.hints)
    except FactorAssistantError as e:
        raise HTTPException(status_code=_map_error_to_status(e), detail=str(e))

    return ok(
        {
            "factor_id": gen.factor_id,
            "display_name": gen.display_name,
            "category": gen.category,
            "description": gen.description,
            "default_params": gen.default_params,
            "code": gen.code,
            "saved_path": gen.saved_path,
        }
    )
