# db.py —— SQLite 持久化：历史任务、对话会话、配置元数据
import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = PROJECT_DIR / "data" / "workbench.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                file_name TEXT,
                file_path TEXT,
                config_snapshot TEXT,
                result_file TEXT,
                result_json TEXT,
                error TEXT,
                log TEXT,
                current_node TEXT,
                total_nodes INTEGER DEFAULT 0,
                elapsed REAL,
                tags TEXT,
                notes TEXT,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_started_at ON tasks(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

            -- 对话会话（附加式：老库启动时自动补建，不需要数据迁移）
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                mode TEXT DEFAULT 'chat',
                model TEXT,
                message_count INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC);

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER,
                role TEXT,
                kind TEXT DEFAULT 'text',
                content TEXT,
                model TEXT,
                tool_name TEXT,
                tool_status TEXT,
                tool_args TEXT,
                tool_result TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, seq);
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_TASK_FIELDS = [
    "task_id", "status", "file_name", "file_path", "config_snapshot",
    "result_file", "result_json", "error", "log", "current_node",
    "total_nodes", "elapsed", "tags", "notes", "started_at", "finished_at"
]


def upsert_task(task: dict):
    """插入或更新任务。dict 中任意字段可以是 Python 对象（自动 JSON 序列化）。"""
    values = []
    for f in _TASK_FIELDS:
        v = task.get(f)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        values.append(v)
    placeholders = ",".join(["?"] * len(_TASK_FIELDS))
    update_clause = ",".join([f"{f}=excluded.{f}" for f in _TASK_FIELDS if f != "task_id"])
    sql = f"""
        INSERT INTO tasks ({",".join(_TASK_FIELDS)})
        VALUES ({placeholders})
        ON CONFLICT(task_id) DO UPDATE SET {update_clause}
    """
    with get_conn() as conn:
        conn.execute(sql, values)


def list_tasks(limit=30, offset=0, status=None, q=None):
    sql = ("SELECT task_id, status, file_name, result_file, error, "
           "total_nodes, elapsed, tags, notes, started_at, finished_at "
           "FROM tasks WHERE 1=1")
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if q:
        sql += " AND (file_name LIKE ? OR task_id LIKE ? OR notes LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_task(task_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("config_snapshot", "result_json", "log", "tags"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def delete_task(task_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))


def update_task_meta(task_id, tags=None, notes=None):
    with get_conn() as conn:
        if tags is not None:
            conn.execute("UPDATE tasks SET tags=? WHERE task_id=?",
                         (json.dumps(tags, ensure_ascii=False), task_id))
        if notes is not None:
            conn.execute("UPDATE tasks SET notes=? WHERE task_id=?", (notes, task_id))


def scan_existing_results(output_dir):
    """把已有 outputs/result_*.json 扫一遍入库，跳过已导入的"""
    imported = 0
    for f in sorted(Path(output_dir).glob("result_*.json")):
        with get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM tasks WHERE result_file=?", (f.name,)
            ).fetchone()
            if exists:
                continue
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
            mtime = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            upsert_task({
                "task_id": f"imported_{f.stem}",
                "status": "success",
                "file_name": data.get("source_file", ""),
                "result_file": f.name,
                "result_json": data,
                "total_nodes": len(data.get("nodes", {})),
                "started_at": mtime,
                "finished_at": mtime,
                "tags": ["imported"],
            })
            imported += 1
        except Exception as e:
            print(f"[db] 导入 {f.name} 失败: {e}")
    return imported


# ============ 对话会话 ============
_CHAT_TOOL_JSON_FIELDS = ("tool_args", "tool_result")


def _decode_chat_message(row: dict) -> dict:
    for k in _CHAT_TOOL_JSON_FIELDS:
        if row.get(k):
            try:
                row[k] = json.loads(row[k])
            except Exception:
                pass
    return row


def _encode_chat_field(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def create_chat_session(session_id=None, title="新对话", mode="chat", model=None):
    session_id = session_id or uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_sessions "
            "(session_id, title, mode, model, message_count, created_at, updated_at) "
            "VALUES (?,?,?,?,0,?,?)",
            (session_id, title, mode, model, now, now),
        )
    return get_chat_session(session_id, with_messages=False)


def get_chat_session(session_id, with_messages=True):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        session = dict(row)
        if with_messages:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id=? ORDER BY seq ASC, id ASC",
                (session_id,),
            ).fetchall()
            session["messages"] = [_decode_chat_message(dict(r)) for r in rows]
    return session


def list_chat_sessions(limit=50, offset=0, q=None):
    sql = ("SELECT session_id, title, mode, model, message_count, created_at, updated_at "
           "FROM chat_sessions WHERE 1=1")
    params = []
    if q:
        sql += " AND title LIKE ?"
        params.append(f"%{q}%")
    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def append_chat_messages(session_id, messages):
    """
    批量追加消息并更新计数/时间；标题为空时用首条用户消息自动命名。

    messages: list[dict]，字段 role/content/kind/model/tool_name/tool_status/tool_args/tool_result
    """
    now = datetime.now().isoformat()
    messages = list(messages or [])
    with get_conn() as conn:
        row = conn.execute(
            "SELECT message_count, title FROM chat_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"会话不存在: {session_id}")

        seq = row["message_count"] or 0
        appended = 0
        for m in messages:
            seq += 1
            conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, seq, role, kind, content, model, tool_name, tool_status, "
                " tool_args, tool_result, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id, seq, m.get("role"), m.get("kind") or "text",
                    m.get("content") or "", m.get("model"),
                    m.get("tool_name"), m.get("tool_status"),
                    _encode_chat_field(m.get("tool_args")),
                    _encode_chat_field(m.get("tool_result")),
                    now,
                ),
            )
            appended += 1

        title = row["title"]
        if not title or title == "新对话":
            first_user = next(
                (m for m in messages
                 if m.get("role") == "user" and (m.get("content") or "").strip()),
                None,
            )
            if first_user:
                flat = " ".join(str(first_user.get("content")).split())
                title = flat[:24] + ("…" if len(flat) > 24 else "")

        conn.execute(
            "UPDATE chat_sessions SET message_count=?, updated_at=?, title=? WHERE session_id=?",
            (seq, now, title, session_id),
        )
    return {"appended": appended, "message_count": seq, "title": title}


def update_chat_session(session_id, title=None, mode=None, model=None):
    sets, params = [], []
    if title is not None:
        sets.append("title=?")
        params.append(title)
    if mode is not None:
        sets.append("mode=?")
        params.append(mode)
    if model is not None:
        sets.append("model=?")
        params.append(model)
    if not sets:
        return get_chat_session(session_id, with_messages=False)
    sets.append("updated_at=?")
    params.append(datetime.now().isoformat())
    params.append(session_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE chat_sessions SET {','.join(sets)} WHERE session_id=?", params)
    return get_chat_session(session_id, with_messages=False)


def delete_chat_session(session_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        cur = conn.execute("DELETE FROM chat_sessions WHERE session_id=?", (session_id,))
        return cur.rowcount > 0