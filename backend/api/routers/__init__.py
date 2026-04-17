"""FastAPI 路由子包。

每个路由模块导出一个 ``router: APIRouter``，由 ``backend.api.main`` 集中 ``include_router``。
分模块拆分而不是单文件：
- 降低单文件代码量，测试 / review 视野更聚焦；
- 未来按领域新增端点（例如 datasets、alerts）时只需加模块，不改 main。
"""
