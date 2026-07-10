"""PG 适配层: ADS pipeline 的「新着物件」(MAIN DB, 旧 Notion 3031) 读写改到
PostgreSQL main.shinchaku_bukken。おすすめ/管理会社判定 等其他 Notion 表不经过这里。

连接: 环境变量 FANGO_MAIN_DSN (默认本机 socket)。
  本机(PG 所在):  FANGO_MAIN_DSN="dbname=fango"
  远程(新机等):   FANGO_MAIN_DSN="host=172.31.212.31 port=5432 dbname=fango user=<rw> password=<pw>"
多线程: 每个 worker 线程各自一条连接 (thread-local)。
"""
from __future__ import annotations
import os, threading
import psycopg

DSN = os.getenv("FANGO_MAIN_DSN", "dbname=fango")

# Notion 属性名 → (PG 列名, Notion 值类型)。只这些是 ADS 回写的评分列。
_MAP = {
    "予測_view数":  ("predicted_view",    "number"),
    "予測_反響数":  ("predicted_inquiry", "number"),
    "推薦点数":     ("recommend_score",   "number"),
    "広告可":       ("ad_ok",             "select"),
    "市場順位":     ("market_rank",       "text"),
    "評価バッチ":   ("eval_batch",        "date"),
}

_local = threading.local()


def _conn():
    c = getattr(_local, "conn", None)
    if c is None or c.closed:
        _local.conn = psycopg.connect(DSN, autocommit=True)
    return _local.conn


def fetch_unscored(cutoff):
    """未评分(predicted_view IS NULL) 且 created_time > cutoff 的行, 最新优先。
    返回每行的 raw (= 完整原始 Notion page 结构), extract_property 可直接用。"""
    sql = ("SELECT raw FROM main.shinchaku_bukken "
           "WHERE predicted_view IS NULL AND created_time > %s "
           "ORDER BY created_time DESC")
    with _conn().cursor() as cur:
        cur.execute(sql, (cutoff,))
        return [row[0] for row in cur.fetchall()]


def _scalar(prop: dict):
    """从 notion_update 格式的属性 dict 取标量值。"""
    if "number" in prop:
        return prop["number"]
    if "select" in prop:
        return (prop.get("select") or {}).get("name")
    if "rich_text" in prop:
        return "".join(x.get("text", {}).get("content", "") for x in prop["rich_text"]) or None
    if "date" in prop:
        return (prop.get("date") or {}).get("start")
    return None


def update_main(page_id: str, props: dict):
    """把 notion_update 同格式的 props 翻译成 PG 列更新 (只更新 _MAP 内的评分列)。"""
    sets, vals = [], []
    for name, prop in props.items():
        m = _MAP.get(name)
        if not m:
            continue
        sets.append(f"{m[0]} = %s")
        vals.append(_scalar(prop))
    if not sets:
        return
    vals.append(page_id)
    sql = f"UPDATE main.shinchaku_bukken SET {', '.join(sets)} WHERE notion_page_id = %s"
    with _conn().cursor() as cur:
        cur.execute(sql, vals)
