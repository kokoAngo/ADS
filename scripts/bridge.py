#!/usr/bin/env python3
"""Claude Bridge CLI — analysis-claude との Notion 経由通信

DB: 「Claude Bridge」 (id は .env の BRIDGE_DB_ID)
Schema: title / body / topic / sender / msg_type / thread_id / read_by_recipient / attachment

Subcommands:
    inbox [--all] [--count-only]   未読メッセージ一覧 (analysis-claude → ops-claude)
    read <page-id>                 全文表示 + 既読化、100KB 超は findings/bridge/ にダンプ
    mark-unread <page-id>          既読化を取り消す
    thread <thread-id>             スレッド内全件を時系列表示
    send <msg_type> <topic> <title> [--dry-run]    新規送信 (body は stdin)
    reply <parent-page-id> <msg_type> [--dry-run]  既存スレへの返信 (body は stdin)
    ack <page-id> [<note>]         軽い ack を送信 (冪等)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
JST = timezone(timedelta(hours=9))
RICH_TEXT_LIMIT = 2000
DUMP_THRESHOLD_BYTES = 100 * 1024

OPS_SENDER = "ops-claude"
OTHER_SENDER = "analysis-claude"
VALID_MSG_TYPES = ("finding", "workflow", "question", "response", "ack")

DUMP_DIR = Path(__file__).resolve().parent.parent / "findings" / "bridge"


def _env(key):
    v = os.getenv(key)
    if not v:
        sys.exit(f"環境変数 {key} 未設定 (.env を確認)")
    return v


def _headers():
    return {
        "Authorization": f"Bearer {_env('BRIDGE_NOTION_API_KEY')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _normalize_page_id(pid):
    s = pid.strip().replace("-", "")
    if len(s) != 32 or not re.fullmatch(r"[0-9a-fA-F]{32}", s):
        sys.exit(f"不正な page_id: {pid}")
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}".lower()


def _request(method, path, **kwargs):
    url = f"{NOTION_API}{path}"
    for attempt in range(2):
        r = requests.request(method, url, headers=_headers(), timeout=30, **kwargs)
        if r.status_code == 429 and attempt == 0:
            time.sleep(1)
            continue
        if r.status_code == 401:
            sys.exit("401 Unauthorized: BRIDGE_NOTION_API_KEY を確認してください")
        if r.status_code == 404:
            sys.exit(f"404 Not Found: {path}")
        if not r.ok:
            sys.exit(f"API エラー {r.status_code}: {r.text[:500]}")
        return r.json()


def _split_body(body, limit=RICH_TEXT_LIMIT):
    """body を limit 文字以下のチャンクに分割。境界優先度: 改行 > 句点 > 空白 > 強制。"""
    if not body:
        return [""]
    if len(body) <= limit:
        return [body]
    chunks = []
    rest = body
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for sep in ("\n", "。", ". ", " "):
            pos = window.rfind(sep)
            if pos > limit // 2:
                cut = pos + len(sep)
                break
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


def _to_rich_text(body):
    return [{"type": "text", "text": {"content": chunk}} for chunk in _split_body(body)]


def _plain(rich_text):
    return "".join(t.get("plain_text", "") for t in rich_text or [])


def _select(prop):
    sel = (prop or {}).get("select")
    return sel.get("name") if sel else ""


def _jst(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def _props_for_send(msg_type, topic, title, body, thread_id=""):
    if msg_type not in VALID_MSG_TYPES:
        sys.exit(f"不正な msg_type: {msg_type} (有効: {VALID_MSG_TYPES})")
    return {
        "title": {"title": [{"type": "text", "text": {"content": title}}]},
        "body": {"rich_text": _to_rich_text(body)},
        "topic": {"rich_text": [{"type": "text", "text": {"content": topic}}]},
        "sender": {"select": {"name": OPS_SENDER}},
        "msg_type": {"select": {"name": msg_type}},
        "thread_id": {
            "rich_text": [{"type": "text", "text": {"content": thread_id}}] if thread_id else []
        },
        "read_by_recipient": {"checkbox": False},
    }


def _extract(page):
    p = page["properties"]
    files = p.get("attachment", {}).get("files", [])
    attachments = []
    for f in files:
        name = f.get("name", "")
        url = (f.get("file") or f.get("external") or {}).get("url", "")
        attachments.append(f"{name}: {url}" if name else url)
    return {
        "page_id": page["id"],
        "title": _plain(p.get("title", {}).get("title")),
        "topic": _plain(p.get("topic", {}).get("rich_text")),
        "sender": _select(p.get("sender")),
        "msg_type": _select(p.get("msg_type")),
        "thread_id": _plain(p.get("thread_id", {}).get("rich_text")),
        "read": p.get("read_by_recipient", {}).get("checkbox", False),
        "body": _plain(p.get("body", {}).get("rich_text")),
        "created": page["created_time"],
        "attachments": attachments,
    }


def _query(filter_obj=None, sort_asc=True, page_size=100):
    payload = {
        "page_size": page_size,
        "sorts": [{
            "timestamp": "created_time",
            "direction": "ascending" if sort_asc else "descending",
        }],
    }
    if filter_obj:
        payload["filter"] = filter_obj
    return _request("POST", f"/databases/{_env('BRIDGE_DB_ID')}/query", json=payload)


def _get_page(page_id):
    return _request("GET", f"/pages/{page_id}")


def _patch_page(page_id, properties):
    return _request("PATCH", f"/pages/{page_id}", json={"properties": properties})


def _create_page(properties):
    return _request("POST", "/pages", json={
        "parent": {"database_id": _env("BRIDGE_DB_ID")},
        "properties": properties,
    })


def _archive_page(page_id):
    return _request("PATCH", f"/pages/{page_id}", json={"archived": True})


def cmd_inbox(args):
    filt_and = [{"property": "sender", "select": {"equals": OTHER_SENDER}}]
    if not args.all:
        filt_and.append({"property": "read_by_recipient", "checkbox": {"equals": False}})
    data = _query(filter_obj={"and": filt_and}, sort_asc=True)
    msgs = [_extract(p) for p in data["results"]]

    if args.count_only:
        sys.stderr.write(f"Bridge inbox 未読: {len(msgs)} 件\n")
        return

    if not msgs:
        print("(受信箱は空です)")
        return

    suffix = "既読含む" if args.all else "未読のみ"
    print(f"Bridge inbox ({len(msgs)} 件) — sender={OTHER_SENDER}, {suffix}")
    print("-" * 80)
    for i, m in enumerate(msgs, 1):
        marker = "✓" if m["read"] else "•"
        thread = f" thread={m['thread_id'][:8]}" if m["thread_id"] else ""
        print(f"{i:2}. {marker} [{m['msg_type']}] {m['title']}")
        print(f"     topic={m['topic']}{thread}  {_jst(m['created'])}")
        print(f"     page_id={m['page_id']}")


def cmd_read(args):
    page_id = _normalize_page_id(args.page_id)
    m = _extract(_get_page(page_id))

    print(f"Title:   {m['title']}")
    print(f"Sender:  {m['sender']}    Type: {m['msg_type']}    Topic: {m['topic']}")
    print(f"Thread:  {m['thread_id'] or '(未確立)'}")
    print(f"Created: {_jst(m['created'])}")
    if m["attachments"]:
        print(f"Attachments ({len(m['attachments'])}):")
        for a in m["attachments"]:
            print(f"  - {a}")
    print("-" * 80)

    body = m["body"]
    body_bytes = len(body.encode("utf-8"))
    if body_bytes > DUMP_THRESHOLD_BYTES:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9_-]+", "_", m["topic"].lower()).strip("_") or "untitled"
        path = DUMP_DIR / f"{slug}_{page_id[:8]}.md"
        path.write_text(body, encoding="utf-8")
        print(f"(本文 {len(body)} 文字 / {body_bytes} bytes — {path} にダンプ)")
    else:
        print(body)

    if not m["read"]:
        _patch_page(page_id, {"read_by_recipient": {"checkbox": True}})
        sys.stderr.write("(read_by_recipient=True にマーク)\n")


def cmd_mark_unread(args):
    page_id = _normalize_page_id(args.page_id)
    _patch_page(page_id, {"read_by_recipient": {"checkbox": False}})
    print(f"既読化を取り消し: {page_id}")


def cmd_thread(args):
    tid = args.thread_id.strip()
    tid_normalized = tid.replace("-", "")
    # thread_id は両表記で query
    candidates = {tid, tid_normalized}
    if len(tid_normalized) == 32:
        try:
            candidates.add(_normalize_page_id(tid_normalized))
        except SystemExit:
            pass
    or_filters = [{"property": "thread_id", "rich_text": {"equals": c}} for c in candidates if c]
    data = _query(filter_obj={"or": or_filters}, sort_asc=True)
    msgs = [_extract(p) for p in data["results"]]
    if not msgs:
        print(f"(thread {tid} のメッセージは見つかりませんでした)")
        return
    print(f"Thread {tid} ({len(msgs)} 件)")
    print("-" * 80)
    for m in msgs:
        marker = "✓" if m["read"] else "•"
        print(f"  {marker} [{m['sender']} / {m['msg_type']}] {m['title']}")
        print(f"      {_jst(m['created'])}  page_id={m['page_id']}")


def _read_stdin():
    if sys.stdin.isatty():
        sys.exit("body は stdin から読みます。例: `echo '本文' | bridge.py send ...`")
    return sys.stdin.read().rstrip("\n")


def cmd_send(args):
    body = _read_stdin()
    props = _props_for_send(args.msg_type, args.topic, args.title, body, thread_id="")
    if args.dry_run:
        print("--- DRY RUN (send) ---")
        print(f"msg_type={args.msg_type}  topic={args.topic}  title={args.title}")
        print(f"body: {len(body)} 文字, {len(_to_rich_text(body))} ブロック分割")
        print("--- body 先頭 500 字 ---")
        print(body[:500] + ("…" if len(body) > 500 else ""))
        return
    page = _create_page(props)
    page_id = page["id"]
    # 自身の page_id を thread_id として書き戻し
    try:
        _patch_page(page_id, {
            "thread_id": {"rich_text": [{"type": "text", "text": {"content": page_id}}]},
        })
    except BaseException:
        # 半端 page を巻き戻し
        try:
            _archive_page(page_id)
        finally:
            raise
    print(f"送信成功 page_id={page_id}  thread_id={page_id} (新スレ)")


def cmd_reply(args):
    parent_id = _normalize_page_id(args.parent_id)
    pm = _extract(_get_page(parent_id))
    thread_id = pm["thread_id"] or parent_id

    body = _read_stdin()
    title = pm["title"] if pm["title"].lower().startswith("re:") else f"Re: {pm['title']}"
    topic = pm["topic"]
    props = _props_for_send(args.msg_type, topic, title, body, thread_id=thread_id)
    if args.dry_run:
        print("--- DRY RUN (reply) ---")
        print(f"parent={parent_id}  thread_id={thread_id}")
        print(f"msg_type={args.msg_type}  topic={topic}  title={title}")
        print("--- body 先頭 500 字 ---")
        print(body[:500] + ("…" if len(body) > 500 else ""))
        return
    page = _create_page(props)
    print(f"返信成功 page_id={page['id']}  thread_id={thread_id}")


def cmd_ack(args):
    parent_id = _normalize_page_id(args.page_id)
    pm = _extract(_get_page(parent_id))
    thread_id = pm["thread_id"] or parent_id

    # 冪等性: 同 thread に既に自分の ack があれば no-op
    existing = _query(filter_obj={
        "and": [
            {"property": "thread_id", "rich_text": {"equals": thread_id}},
            {"property": "sender", "select": {"equals": OPS_SENDER}},
            {"property": "msg_type", "select": {"equals": "ack"}},
        ]
    })
    if existing["results"]:
        print(f"既に ack 済み (thread {thread_id[:8]}…) — skip")
        if not pm["read"]:
            _patch_page(parent_id, {"read_by_recipient": {"checkbox": True}})
        return

    note = args.note or "受領しました"
    body = f"ack: {note}\n\n親メッセージ: {pm['title']} ({pm['msg_type']} / topic={pm['topic']})"
    title = f"Ack: {pm['title']}"
    props = _props_for_send("ack", pm["topic"], title, body, thread_id=thread_id)
    page = _create_page(props)
    if not pm["read"]:
        _patch_page(parent_id, {"read_by_recipient": {"checkbox": True}})
    print(f"ack 送信成功 page_id={page['id']}  thread_id={thread_id}")


def main():
    parser = argparse.ArgumentParser(description="Claude Bridge CLI (analysis-claude との Notion 通信)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inbox", help="未読一覧 (analysis-claude → ops-claude)")
    p.add_argument("--all", action="store_true", help="既読も含める")
    p.add_argument("--count-only", action="store_true", help="件数だけを stderr に出力")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("read", help="メッセージ全文表示 + 既読化")
    p.add_argument("page_id")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("mark-unread", help="既読化を取り消す")
    p.add_argument("page_id")
    p.set_defaults(func=cmd_mark_unread)

    p = sub.add_parser("thread", help="スレッド全件を表示")
    p.add_argument("thread_id")
    p.set_defaults(func=cmd_thread)

    p = sub.add_parser("send", help="新規発信 (body は stdin)")
    p.add_argument("msg_type", choices=VALID_MSG_TYPES)
    p.add_argument("topic")
    p.add_argument("title")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("reply", help="既存スレへ返信 (body は stdin)")
    p.add_argument("parent_id")
    p.add_argument("msg_type", choices=VALID_MSG_TYPES)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("ack", help="軽い ack を送信 (冪等)")
    p.add_argument("page_id")
    p.add_argument("note", nargs="?", default=None)
    p.set_defaults(func=cmd_ack)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
