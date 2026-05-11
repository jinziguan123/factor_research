"""从 Akshare 同步申万行业归属到 fr_industry_current.sw_l1/sw_l2/sw_l3。

策略：
1. sw_index_third_info() + sw_index_second_info() 建立 L3→L2→L1 层级映射
2. 遍历 31 个 L1 行业 index_component_sw(code) 获取 stock→L1 映射（兜底）
3. 遍历 336 个 L3 行业 index_component_sw(code) 获取 stock→L3 映射
4. 通过层级表反推 L2/L1

每次 API 调用间隔 0.5s 防限流。总耗时 ~8-10 分钟，适合后台任务。
"""
from __future__ import annotations

import logging
import time

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

CALL_INTERVAL = 0.5


def _safe_normalize(raw_code: str) -> str | None:
    s = str(raw_code).strip()
    if len(s) == 6:
        try:
            return normalize_symbol(s)
        except ValueError:
            return None
    return None


def fetch_sw_hierarchy() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """构建申万三级行业层级映射。

    Returns:
        (l3_code_to_name, l3_name_to_l2, l2_name_to_l1)
    """
    import akshare as ak

    df_l2 = ak.sw_index_second_info()
    df_l3 = ak.sw_index_third_info()

    l2_name_to_l1: dict[str, str] = {}
    for _, row in df_l2.iterrows():
        l2_name = str(row["行业名称"]).strip()
        l1_name = str(row["上级行业"]).strip()
        if l2_name and l1_name:
            l2_name_to_l1[l2_name] = l1_name

    l3_code_to_name: dict[str, str] = {}
    l3_name_to_l2: dict[str, str] = {}
    for _, row in df_l3.iterrows():
        code = str(row["行业代码"]).replace(".SI", "").strip()
        l3_name = str(row["行业名称"]).strip()
        l2_name = str(row["上级行业"]).strip()
        if code and l3_name:
            l3_code_to_name[code] = l3_name
        if l3_name and l2_name:
            l3_name_to_l2[l3_name] = l2_name

    log.info(
        "sw hierarchy: %d L3 industries, %d L2, %d L1 mappings",
        len(l3_code_to_name), len(l3_name_to_l2), len(l2_name_to_l1),
    )
    return l3_code_to_name, l3_name_to_l2, l2_name_to_l1


def fetch_sw_industry_all() -> dict[str, dict[str, str]]:
    """遍历申万行业获取全市场 stock→{sw_l1, sw_l2, sw_l3} 映射。"""
    import akshare as ak

    l3_code_to_name, l3_name_to_l2, l2_name_to_l1 = fetch_sw_hierarchy()

    result: dict[str, dict[str, str]] = {}

    # Phase 1: 遍历 L1 行业（兜底映射）
    df_l1 = ak.sw_index_first_info()
    l1_codes: list[tuple[str, str]] = []
    for _, row in df_l1.iterrows():
        code = str(row["行业代码"]).replace(".SI", "").strip()
        name = str(row["行业名称"]).strip()
        if code and name:
            l1_codes.append((code, name))

    log.info("fetching L1 constituents for %d industries...", len(l1_codes))
    for code, l1_name in l1_codes:
        try:
            df_cons = ak.index_component_sw(symbol=code)
            time.sleep(CALL_INTERVAL)
        except Exception as e:
            log.warning("index_component_sw(%s) failed: %s", code, e)
            time.sleep(CALL_INTERVAL)
            continue

        if df_cons is None or df_cons.empty:
            continue

        for _, row in df_cons.iterrows():
            raw = str(row.get("证券代码", "")).strip()
            symbol = _safe_normalize(raw)
            if symbol and symbol not in result:
                result[symbol] = {"sw_l1": l1_name, "sw_l2": "", "sw_l3": ""}

    log.info("L1 pass done: %d stocks mapped", len(result))

    # Phase 2: 遍历 L3 行业（覆盖写入完整三级）
    l3_codes = list(l3_code_to_name.items())
    log.info("fetching L3 constituents for %d industries...", len(l3_codes))
    for i, (code, l3_name) in enumerate(l3_codes):
        try:
            df_cons = ak.index_component_sw(symbol=code)
            time.sleep(CALL_INTERVAL)
        except Exception as e:
            log.warning("index_component_sw(%s/%s) failed: %s", code, l3_name, e)
            time.sleep(CALL_INTERVAL)
            continue

        if df_cons is None or df_cons.empty:
            continue

        l2_name = l3_name_to_l2.get(l3_name, "")
        l1_name = l2_name_to_l1.get(l2_name, "")

        for _, row in df_cons.iterrows():
            raw = str(row.get("证券代码", "")).strip()
            symbol = _safe_normalize(raw)
            if symbol:
                result[symbol] = {"sw_l1": l1_name, "sw_l2": l2_name, "sw_l3": l3_name}

        if (i + 1) % 50 == 0:
            log.info("L3 progress: %d/%d", i + 1, len(l3_codes))

    log.info("L3 pass done: %d stocks total", len(result))
    return result


def upsert_sw_industry(mapping: dict[str, dict[str, str]]) -> dict[str, int]:
    """将申万行业映射写入 fr_industry_current。"""
    update_sql = (
        "UPDATE fr_industry_current "
        "SET sw_l1=%s, sw_l2=%s, sw_l3=%s "
        "WHERE symbol=%s"
    )
    insert_sql = (
        "INSERT IGNORE INTO fr_industry_current "
        "(symbol, sw_l1, sw_l2, sw_l3, industry_l1, industry_classification, "
        " snapshot_date, data_source) "
        "VALUES (%s, %s, %s, %s, '', '', CURDATE(), 'akshare')"
    )

    updated = 0
    inserted = 0

    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for symbol, info in mapping.items():
                cur.execute(update_sql, (info["sw_l1"], info["sw_l2"], info["sw_l3"], symbol))
                if cur.rowcount == 0:
                    cur.execute(insert_sql, (symbol, info["sw_l1"], info["sw_l2"], info["sw_l3"]))
                    inserted += 1
                else:
                    updated += 1
        conn.commit()

    log.info("sw industry upsert: updated=%d, inserted=%d", updated, inserted)
    return {"updated": updated, "inserted": inserted, "total": updated + inserted}


def sync_sw_industry() -> dict[str, int]:
    """入口：拉取全市场申万行业归属并 upsert。"""
    mapping = fetch_sw_industry_all()
    return upsert_sw_industry(mapping)
