"""数据源适配器。

本包提供**外部数据源 → 本平台标准 schema** 的单向转换层。每个数据源一个子包
（如 ``baostock/``），内部隔离各自的连接、字段名、symbol 编码等差异；对外只暴
露与 ``backend.adapters.base`` 中定义的抽象一致的接口。

使用入口：
- 业务层永远通过 ``backend.storage.*`` 读经过 adapter 规范化之后的本地表，
  **不直接调 adapter**；
- 运维侧通过 ``backend.api.routers.admin`` 的 ``:sync`` 端点（或相应脚本）
  触发 adapter 写入。
"""
