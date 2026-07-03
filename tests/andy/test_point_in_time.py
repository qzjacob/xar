"""Point-in-time 正确性（需 DB）：证明前视防护——读到的永远是"那天能知道的值"。"""
from __future__ import annotations

from datetime import date

import pytest

from tests.andy.conftest import requires_db


@requires_db
def test_no_look_ahead_bias(seeded, conn):
    from slx.engine.point_in_time import PointInTimeContext

    # labor.labor_share 2025Q1：首发 0.582（knowledge 2025-04-30），修订 0.575（knowledge 2025-07-30）
    before_revision = PointInTimeContext(conn, date(2025, 5, 15)).value("labor.labor_share")
    after_revision = PointInTimeContext(conn, date(2025, 8, 15)).value("labor.labor_share")
    assert before_revision == 0.582, "as_of 在修订前应读到首发值"
    assert after_revision == 0.575, "as_of 在修订后应读到修订值"
    assert before_revision != after_revision


@requires_db
def test_no_data_before_first_release(seeded, conn):
    from slx.engine.point_in_time import NoData, PointInTimeContext

    with pytest.raises(NoData):
        PointInTimeContext(conn, date(2025, 1, 1)).value("labor.labor_share")


@requires_db
def test_pit_result_is_stable_over_wall_clock(seeded, conn):
    """同一 as_of 的查询结果不随系统当前时间漂移（可复现）。"""
    from slx.engine.point_in_time import PointInTimeContext

    a = PointInTimeContext(conn, date(2025, 5, 15)).value("labor.labor_share")
    b = PointInTimeContext(conn, date(2025, 5, 15)).value("labor.labor_share")
    assert a == b == 0.582
