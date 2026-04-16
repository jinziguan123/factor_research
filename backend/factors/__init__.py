"""因子代码目录：每个子目录一个 category，内部文件定义继承 BaseFactor 的因子类。

约定：
- 子目录名即 ``category``（reversal / momentum / volatility / volume / custom ...）；
- 每个因子一个 ``.py`` 文件，文件内定义一个或多个 ``BaseFactor`` 子类；
- FactorRegistry 会递归扫描本目录，自动注册所有符合条件的子类。
"""
