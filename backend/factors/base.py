"""重导出 BaseFactor / FactorContext。

因子实现文件习惯写 ``from backend.factors.base import BaseFactor``——这样直觉上
从同一个包引入基类；真实定义仍在 ``backend.engine.base_factor``，本模块只做薄
re-export，便于后续基类迁移时降低修改面。
"""
from backend.engine.base_factor import BaseFactor, FactorContext

__all__ = ["BaseFactor", "FactorContext"]
