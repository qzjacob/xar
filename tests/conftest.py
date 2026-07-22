"""Shared pytest fixtures for the XAR suite."""
import pytest


@pytest.fixture(scope="session")
def seeded_db():
    """Ensure schema + companies + seed graph exist (idempotent). Session-scoped so the
    one-time seed is shared; requesting tests get a populated local Postgres."""
    from xar.ingestion import seed_companies
    from xar.kg import store
    from xar.storage import db
    db.init_schema()
    seed_companies()
    store.bootstrap_seed()
    return db


@pytest.fixture()
def isolated_db(seeded_db, monkeypatch):
    """事务回滚隔离(K.3.2 测试隔离基建)。本测试**及被测代码**的所有 `db` 读写跑在**单个连接的
    一个事务**里,测试结束整体 ROLLBACK —— 三重收益:
      ① 测试之间零污染、跨 pytest 运行零残留(写入永不落库);
      ② 测试可在事务内**临时 DELETE 既有数据(含生产行)**校验「干净聚合」而绝不落库(rollback 复原)——
         解决 test_calibration_buckets(生产 verdict 污染全局桶计数)、test_link_idempotent_cursor
         (跨运行 kg_events 累积)这类「聚合读到不属于本测试的数据」的红。
    机制:从池取一条连接、开事务;把 `db.conn`/`db.tx` 猴补为「产出该连接代理、退出不提交/不关闭」的
    上下文管理器(代理的 commit 为 no-op → 被测 `db.execute` 的 commit 不真正落库,维持单事务);
    teardown 真 rollback + 归还连接(已回滚,干净,不污染池)。**不 autouse**——仅显式请求它的测试生效,
    避免影响依赖已提交数据/tx 语义的既有测试。"""
    from xar.storage import db

    cm = db.pool().connection()
    real = cm.__enter__()
    try:
        from pgvector.psycopg import register_vector
        register_vector(real)
    except Exception:  # noqa: BLE001 — vector 扩展缺失由 init_schema 处理
        pass

    class _Pinned:                              # 代理:commit no-op(维持单事务),其余委托真连接
        def commit(self):
            pass

        def rollback(self):
            real.rollback()

        def __getattr__(self, name):
            return getattr(real, name)

    pinned = _Pinned()

    class _PinnedCM:                            # 供被测代码的 `with db.conn()/db.tx() as c:`
        def __enter__(self):
            return pinned

        def __exit__(self, *exc):               # 不提交、不关闭 —— fixture 统一回滚
            return False

    monkeypatch.setattr(db, "conn", _PinnedCM)
    monkeypatch.setattr(db, "tx", _PinnedCM)
    try:
        yield db
    finally:
        try:
            real.rollback()                     # 撤销本测试事务内一切写入(含临时删的既有行→复原)
        finally:
            cm.__exit__(None, None, None)        # 归还连接到池(已回滚,干净)
