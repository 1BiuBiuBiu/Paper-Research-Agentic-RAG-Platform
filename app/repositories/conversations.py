import json
from uuid import uuid4

from app.core.errors import AppError
from app.domain.agent import AgentTurnInput
from app.domain.papers import utc_now
from app.repositories.papers import PaperRepository
from app.repositories.project_schema import initialize_projects, require_project


class ConversationRepository:
    def __init__(self, papers: PaperRepository) -> None:
        self.papers = papers
        with papers.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, "
                "title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT "
                "NOT NULL, revision INTEGER NOT NULL DEFAULT 0, archived INTEGER "
                "NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversation_turns (id TEXT PRIMARY "
                "KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), "
                "request TEXT NOT NULL, response TEXT, status TEXT NOT NULL, "
                "error_code TEXT, error_message TEXT, created_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_turn_conversation ON "
                "conversation_turns(conversation_id,created_at)"
            )
            if db.execute("PRAGMA user_version").fetchone()[0] < 4:
                db.execute("PRAGMA user_version=4")
            initialize_projects(db)

    def create(self, title: str, project_id: str | None = None) -> dict:
        identifier, now = str(uuid4()), utc_now()
        with self.papers.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            require_project(db, project_id)
            db.execute(
                "INSERT INTO conversations (id,title,created_at,updated_at,project_id) "
                "VALUES(?,?,?,?,?)",
                (identifier, title, now, now, project_id),
            )
        return self.get(identifier)

    def list(
        self,
        offset: int = 0,
        limit: int = 100,
        *,
        project_id: str | None = None,
        unassigned: bool = False,
    ) -> list[dict]:
        with self.papers.connect() as db:
            require_project(db, project_id)
            where, args = "archived=0", []
            if project_id is not None:
                where += " AND project_id=?"
                args.append(project_id)
            elif unassigned:
                where += " AND project_id IS NULL"
            rows = db.execute(
                "SELECT id,title,updated_at,revision,project_id FROM conversations WHERE "
                + where
                + " ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        keys = ("id", "title", "updated_at", "revision", "project_id")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def get(self, identifier: str) -> dict:
        with self.papers.connect() as db:
            row = db.execute(
                (
                    "SELECT id,title,created_at,updated_at,revision,archived,project_id FROM "
                    "conversations WHERE id=?"
                ),
                (identifier,),
            ).fetchone()
            if not row:
                raise AppError("conversation_not_found", "对话不存在。", 404)
            turns = db.execute(
                (
                    "SELECT id,request,response,status,error_code,error_message,c"
                    "reated_at FROM conversation_turns WHERE conversation_id=? ORDER "
                    "BY rowid"
                ),
                (identifier,),
            ).fetchall()
        result = dict(
            zip(
                ("id", "title", "created_at", "updated_at", "revision", "archived", "project_id"),
                row,
                strict=True,
            )
        )
        result["turns"] = [self._turn(t) for t in turns]
        return result

    @staticmethod
    def _turn(row: tuple) -> dict:
        return {
            "id": row[0],
            "request": json.loads(row[1]),
            "response": json.loads(row[2]) if row[2] else None,
            "status": row[3],
            "error_code": row[4],
            "error_message": row[5],
            "created_at": row[6],
        }

    def rename(self, identifier: str, title: str) -> dict:
        return self.update(identifier, {"title": title})

    def update(self, identifier: str, changes: dict) -> dict:
        with self.papers.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT title,project_id FROM conversations WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise AppError("conversation_not_found", "对话不存在。", 404)
            project_id = changes.get("project_id", row[1])
            require_project(db, project_id)
            db.execute(
                "UPDATE conversations SET title=?,project_id=?,updated_at=? WHERE id=?",
                (changes.get("title", row[0]), project_id, utc_now(), identifier),
            )
        return self.get(identifier)

    def begin(self, identifier: str, request: AgentTurnInput) -> dict | None:
        encoded = request.model_dump_json()
        with self.papers.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision,archived FROM conversations WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                raise AppError("conversation_not_found", "对话不存在。", 404)
            old = db.execute(
                "SELECT conversation_id,request,response,status FROM conversation_turns WHERE id=?",
                (str(request.request_id),),
            ).fetchone()
            if old and (old[0] != identifier or old[1] != encoded):
                raise AppError("request_conflict", "请求 ID 已用于其他内容。", 409)
            if old and old[3] == "completed":
                return json.loads(old[2])
            if row[0] != request.expected_revision:
                raise AppError("conversation_changed", "对话已在其他窗口更新，请刷新后重试。", 409)
            if db.execute(
                "SELECT 1 FROM conversation_turns WHERE conversation_id=? AND status='pending'",
                (identifier,),
            ).fetchone():
                raise AppError("conversation_busy", "该对话正在回答，请等待完成。", 409)
            if old:
                db.execute(
                    (
                        "UPDATE conversation_turns SET status='pending',error_code=NU"
                        "LL,error_message=NULL WHERE id=?"
                    ),
                    (str(request.request_id),),
                )
            else:
                db.execute(
                    "INSERT INTO conversation_turns VALUES(?,?,?,NULL,'pending',NULL,NULL,?)",
                    (str(request.request_id), identifier, encoded, utc_now()),
                )
        return None

    def finish(self, identifier: str, request_id: str, response: dict, question: str = "") -> None:
        with self.papers.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                (
                    "UPDATE conversation_turns SET response=?,status='completed' "
                    "WHERE id=? AND conversation_id=? AND status='pending'"
                ),
                (json.dumps(response, ensure_ascii=False), request_id, identifier),
            )
            if updated.rowcount != 1:
                raise AppError("turn_changed", "本轮状态已变化，未保存为成功。", 409)
            db.execute(
                "UPDATE conversations SET revision=revision+1,updated_at=?,"
                "title=CASE WHEN revision=0 AND title='新对话' AND ?<>'' THEN ? ELSE title END "
                "WHERE id=?",
                (
                    utc_now(),
                    " ".join(question.split())[:40],
                    " ".join(question.split())[:40],
                    identifier,
                ),
            )

    def fail(self, request_id: str, error: AppError) -> None:
        with self.papers.connect() as db:
            db.execute(
                (
                    "UPDATE conversation_turns SET status='failed',error_code=?,e"
                    "rror_message=? WHERE id=? AND status='pending'"
                ),
                (error.code, error.message, request_id),
            )

    def recover_interrupted(self) -> None:
        with self.papers.connect() as db:
            db.execute(
                "UPDATE conversation_turns SET status='failed',error_code='in"
                "terrupted',error_message='服务重启中断了本轮，请手动重试。' WHERE "
                "status='pending'"
            )
