import asyncio
import sqlite3
import importlib

from sqlalchemy.dialects import sqlite

models = importlib.import_module("gsuid_core.plugins.MajsoulUID.MajsoulUID.utils.database.models")

GROUP_A = "1130845366158377063"
GROUP_B = "1246842766206435410"


class Rows:
    def __init__(self, rows: list[tuple]):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return [row[0] for row in self.rows]


class SqliteSession:
    """把 SQLAlchemy 语句编译成 SQL，在内存库上执行"""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    async def execute(self, stmt):
        sql = str(stmt.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
        return Rows(self.db.execute(sql).fetchall())


def make_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE majsbind (bot_id TEXT, user_id TEXT, group_id TEXT, uid TEXT)")
    db.execute("CREATE TABLE coreuser (bot_id TEXT, user_id TEXT, group_id TEXT)")
    db.executemany(
        "INSERT INTO majsbind VALUES (?, ?, ?, ?)",
        [
            # 在 A 群绑定，后来也在 B 群说过话
            ("discord", "alice", GROUP_A, "19900120"),
            # 在 A 群绑定了两个号，从没在 B 群出现
            ("discord", "bob", GROUP_A, "18543983_18537431"),
            # 绑定记录没有群号，只在 B 群说过话
            ("discord", "carol", None, "12536235"),
            # 别的平台同名用户，不能算进来
            ("onebot", "carol", None, "218174"),
            ("discord", "dave", GROUP_B, None),
        ],
    )
    db.executemany(
        "INSERT INTO coreuser VALUES (?, ?, ?)",
        [("discord", "alice", GROUP_A), ("discord", "alice", GROUP_B), ("discord", "carol", GROUP_B)],
    )
    return db


def group_uids(group_id: str, bot_id: str = "discord") -> set[str]:
    fn = models.MajsBind.get_group_uids.__wrapped__
    return asyncio.run(fn(models.MajsBind, SqliteSession(make_db()), bot_id, bot_id, group_id))


def test_group_sees_only_its_own_members():
    assert group_uids(GROUP_A) == {"19900120", "18543983", "18537431"}
    assert group_uids(GROUP_B) == {"19900120", "12536235"}


def test_unknown_group_is_empty():
    assert group_uids("999") == set()


def test_other_platform_is_excluded():
    assert group_uids(GROUP_B, bot_id="onebot") == set()
