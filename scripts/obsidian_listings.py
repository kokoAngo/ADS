#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Obsidian Listings — 物件単位の登録店舗数 時系列ノート I/O.

1 物件 = 1 ノート。ファイル名は `<reins_id>_<sanitized_name>.md`。
reins_id を primary key にすることで、Notion 側で物件名が変わっても同一物件として履歴を継続。

frontmatter (手書き YAML、依存追加なし):
    ---
    reins_id: 100139035093
    property_name: ミモザパル
    koukai_date: 2026-05-08
    first_seen: 2026-05-14 16:38
    last_updated: 2026-05-14 16:38
    ---

本文 (新しい行を table 先頭に挿入):
    # ミモザパル

    ## 登録店舗数履歴

    | timestamp        | count | status        |
    |------------------|-------|---------------|
    | 2026-05-14 16:38 | 41    | 掲載指示済み |

watch_registrations.py から `append_observation()` で呼ばれる想定。
"""
import os
import re
from pathlib import Path


_INVALID_FN_CHARS = re.compile(r'[/:\\<>|*?"]')

OBSIDIAN_VAULT = Path(
    os.getenv("OBSIDIAN_VAULT", "/Users/developer_recika/Documents/Obsidian Vault")
)
LISTINGS_DIR = OBSIDIAN_VAULT / "Fango Listings"

_HEADER_LINE = "| timestamp        | count | status        |"
_SEP_LINE    = "|------------------|-------|---------------|"
_FRONTMATTER_KEYS = ("reins_id", "property_name", "koukai_date", "first_seen", "last_updated")


def sanitize_filename(name: str) -> str:
    """物件名 → ファイル名安全化 (PVMonitor obsidian_store.sanitize_filename と同一規則)."""
    if not name:
        return "_unknown"
    cleaned = _INVALID_FN_CHARS.sub("_", name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "_unknown"


def _find_or_new_path(reins_id: str, property_name: str) -> Path:
    """既存 `<reins_id>_*.md` があればそれを返す。物件名変更があってもファイル発見可能。"""
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(LISTINGS_DIR.glob(f"{reins_id}_*.md"))
    if existing:
        return existing[0]
    return LISTINGS_DIR / f"{reins_id}_{sanitize_filename(property_name)}.md"


def _parse(text: str) -> tuple[dict, list[str]]:
    """既存ノートから frontmatter dict と本文行リストを取り出す."""
    meta: dict = {}
    lines = text.splitlines()
    body_start = 0

    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                for kv in lines[1:i]:
                    m = re.match(r"^(\w+):\s*(.*)$", kv)
                    if m:
                        meta[m.group(1)] = m.group(2).strip()
                body_start = i + 1
                break

    while body_start < len(lines) and lines[body_start].strip() == "":
        body_start += 1

    return meta, lines[body_start:]


def _render(meta: dict, body_lines: list[str]) -> str:
    fm_lines = ["---"]
    for k in _FRONTMATTER_KEYS:
        v = meta.get(k, "")
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    fm_lines.append("")
    return "\n".join(fm_lines + body_lines) + "\n"


def _insert_row_at_table_top(body_lines: list[str], new_row: str) -> list[str]:
    """`|---|---|...` セパレータ直後に new_row を挿入. table が無ければ末尾に追加."""
    for i, line in enumerate(body_lines):
        if set(line.replace("|", "").strip()) <= set("-: ") and line.strip().startswith("|"):
            return body_lines[: i + 1] + [new_row] + body_lines[i + 1 :]
    return body_lines + [_HEADER_LINE, _SEP_LINE, new_row]


def append_observation(
    reins_id: str,
    property_name: str,
    koukai_date: str,
    timestamp: str,
    count: int | None,
    status: str,
) -> Path | None:
    """登録店舗数の観測を 1 行 append. count is None なら何もしない."""
    if count is None:
        return None
    if not reins_id:
        return None

    p = _find_or_new_path(reins_id, property_name)

    if p.exists():
        meta, body_lines = _parse(p.read_text(encoding="utf-8"))
        meta["last_updated"] = timestamp
        if property_name:
            meta["property_name"] = property_name
        if koukai_date and not meta.get("koukai_date"):
            meta["koukai_date"] = koukai_date
        meta.setdefault("reins_id", reins_id)
        meta.setdefault("first_seen", timestamp)
        if body_lines and body_lines[0].startswith("# ") and property_name:
            body_lines[0] = f"# {property_name}"
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "reins_id": reins_id,
            "property_name": property_name,
            "koukai_date": koukai_date or "",
            "first_seen": timestamp,
            "last_updated": timestamp,
        }
        body_lines = [
            f"# {property_name}",
            "",
            "## 登録店舗数履歴",
            "",
            _HEADER_LINE,
            _SEP_LINE,
        ]

    new_row = f"| {timestamp:<16} | {count:<5} | {status:<13} |"
    body_lines = _insert_row_at_table_top(body_lines, new_row)
    p.write_text(_render(meta, body_lines), encoding="utf-8")
    return p
