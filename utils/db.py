"""
统一 SQLite 连接管理
====================
全项目(四库 / 技能使用记录 / 任务统计 / 代码符号索引)共享同一个
nexus.db 连接, 解决多连接并发写 "database is locked" 的问题:

- 单例连接, ``check_same_thread=False`` 配合 RLock 串行化写入
- WAL 日志模式: 读写不互斥, 多线程下读不阻塞写、写不阻塞读
- ``busy_timeout=5000ms``: 偶发锁等待时自动重试而非立刻报错
- ``synchronous=NORMAL``: WAL 下安全且更快

使用方式::

    from utils.db import get_db
    db = get_db()
    db.execute("INSERT INTO ... VALUES (?, ?)", (a, b))   # 自动提交的写
    rows = db.query("SELECT ... WHERE id=?", (1,))        # 读
    with db.transaction():                                # 多语句事务
        db.conn.execute(...); db.conn.execute(...)
"""
import sqlite3
import threading
from contextlib import contextmanager

from config import DATA_DIR

DB_PATH = DATA_DIR / "nexus.db"


class DatabaseManager:
    """进程内单例的 SQLite 连接与写锁管理"""

    _instance: "DatabaseManager | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init_connection()
                    cls._instance = instance
        return cls._instance

    def _init_connection(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        # WAL: 多线程读写并发; busy_timeout: 锁等待兜底; NORMAL: WAL 下兼顾安全与速度
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self):
        """多语句写事务: 异常自动回滚, 正常自动提交 (可重入)"""
        with self._write_lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        """单条写操作, 自动提交"""
        with self._write_lock:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor

    def executemany(self, sql: str, seq) -> sqlite3.Cursor:
        """批量写操作, 一次提交"""
        with self._write_lock:
            cursor = self.conn.executemany(sql, seq)
            self.conn.commit()
            return cursor

    def query(self, sql: str, params: tuple | list = ()) -> list:
        """查询, 返回全部行"""
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | list = ()):
        """查询, 返回单行"""
        return self.conn.execute(sql, params).fetchone()


def get_db() -> DatabaseManager:
    """获取全局唯一的数据库管理器"""
    return DatabaseManager()
