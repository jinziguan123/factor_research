"""Baostock 数据源适配器。

对 baostock SDK 做一层薄封装：
- 统一登录 / 登出上下文（``client.baostock_session``）；
- 每类数据一个模块（``instruments`` / ``calendar`` / 后续 ``industry`` / ``index`` …），
  产出规范化后的 dict 列表，由上层灌入本地表；
- symbol 统一走 ``adapters.base.normalize_symbol`` 转成 QMT 格式，不向外暴露
  Baostock 的 ``sh.600000`` 风格。

注意：baostock 不是 pip 默认会装的包；pyproject.toml 已把它列进 dependencies，
若执行期 import 失败则 ``sync_*`` 函数会抛 ``ModuleNotFoundError``，提醒先装包。
"""
