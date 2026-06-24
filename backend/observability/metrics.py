"""轻量 Prometheus 指标导出（零第三方依赖，手写文本格式 0.0.4）。

提供 Counter / Gauge / Histogram 与全局 ``REGISTRY``，``GET /metrics`` 渲染为
Prometheus 可抓取文本。仅用标准库，便于单测；生产若需更全功能可平滑替换为
``prometheus_client``（指标名 / 标签已按其约定）。
"""
from __future__ import annotations

import threading


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_labels(d: dict) -> str:
    if not d:
        return ""
    return "{" + ",".join(f'{k}="{_escape(str(v))}"' for k, v in d.items()) + "}"


def _num(v: float) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


def _fmt_le(b: float) -> str:
    f = float(b)
    return str(int(f)) if f.is_integer() else repr(f)


class _Base:
    def __init__(self, name: str, help: str, labelnames=()) -> None:
        self.name = name
        self.help = help
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()

    def _key(self, labels: dict) -> tuple:
        return tuple(str(labels.get(ln, "")) for ln in self.labelnames)

    def _labels_dict(self, key: tuple) -> dict:
        return dict(zip(self.labelnames, key))


class Counter(_Base):
    """单调递增计数器。"""

    def __init__(self, name, help, labelnames=()) -> None:
        super().__init__(name, help, labelnames)
        self._v: dict[tuple, float] = {}

    def inc(self, amount: float = 1.0, **labels) -> None:
        k = self._key(labels)
        with self._lock:
            self._v[k] = self._v.get(k, 0.0) + amount

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        for k, v in sorted(self._v.items()):
            out.append(f"{self.name}{_fmt_labels(self._labels_dict(k))} {_num(v)}")
        return out


class Gauge(_Base):
    """可增可减的瞬时值。"""

    def __init__(self, name, help, labelnames=()) -> None:
        super().__init__(name, help, labelnames)
        self._v: dict[tuple, float] = {}

    def set(self, value: float, **labels) -> None:
        k = self._key(labels)
        with self._lock:
            self._v[k] = float(value)

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        for k, v in sorted(self._v.items()):
            out.append(f"{self.name}{_fmt_labels(self._labels_dict(k))} {_num(v)}")
        return out


_DEFAULT_BUCKETS = (0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)


class Histogram(_Base):
    """直方图：累积 bucket + _sum + _count（Prometheus 约定）。"""

    def __init__(self, name, help, labelnames=(), buckets=_DEFAULT_BUCKETS) -> None:
        super().__init__(name, help, labelnames)
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[tuple, list[int]] = {}
        self._sum: dict[tuple, float] = {}
        self._cnt: dict[tuple, int] = {}

    def observe(self, value: float, **labels) -> None:
        k = self._key(labels)
        with self._lock:
            if k not in self._counts:
                self._counts[k] = [0] * len(self.buckets)
                self._sum[k] = 0.0
                self._cnt[k] = 0
            for i, b in enumerate(self.buckets):
                if value <= b:
                    self._counts[k][i] += 1  # 累积语义：≤b 的所有 bucket 计数
            self._sum[k] += float(value)
            self._cnt[k] += 1

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for k in sorted(self._counts):
            base = self._labels_dict(k)
            for i, b in enumerate(self.buckets):
                d = {**base, "le": _fmt_le(b)}
                out.append(f"{self.name}_bucket{_fmt_labels(d)} {self._counts[k][i]}")
            out.append(f"{self.name}_bucket{_fmt_labels({**base, 'le': '+Inf'})} {self._cnt[k]}")
            out.append(f"{self.name}_sum{_fmt_labels(base)} {_num(self._sum[k])}")
            out.append(f"{self.name}_count{_fmt_labels(base)} {self._cnt[k]}")
        return out


class MetricsRegistry:
    """注册并渲染一组指标。"""

    def __init__(self) -> None:
        self._metrics: list = []

    def counter(self, name, help, labelnames=()) -> Counter:
        m = Counter(name, help, labelnames)
        self._metrics.append(m)
        return m

    def gauge(self, name, help, labelnames=()) -> Gauge:
        m = Gauge(name, help, labelnames)
        self._metrics.append(m)
        return m

    def histogram(self, name, help, labelnames=(), buckets=_DEFAULT_BUCKETS) -> Histogram:
        m = Histogram(name, help, labelnames, buckets)
        self._metrics.append(m)
        return m

    def render(self) -> str:
        lines: list[str] = []
        for m in self._metrics:
            lines.extend(m.render())
        return "\n".join(lines) + "\n"


# ---------------------------- 全局 registry ----------------------------

# 注意：任务计数 / 时延 / 数据健康改由 ``observability/db_metrics.py`` 从 MySQL 的
# fr_*_runs 表实时派生——回测/评估等任务在 ProcessPool worker 子进程执行，其进程内
# 计数器无法被主进程 /metrics 读取（内存隔离）；从持久化的 DB 状态派生则跨进程一致、
# 重启不丢。此 REGISTRY 保留给未来真正在主进程内累积的指标（如 HTTP 请求计数）。
REGISTRY = MetricsRegistry()
