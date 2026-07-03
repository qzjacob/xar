"""实接连接器集 —— 每个模块一个数据源，均继承 ingestion.base.Connector 实现 fetch()。

约定（与 base.Connector 一致）：
  - fetch() 返回 observation 行 dict：必填 metric_key, valid_time, value；
    选填 source_id, knowledge_time, vintage_date, unit, value_low, value_high。
  - 需 key 的源从 os.environ 读取；缺 key 时清晰报错或跳过并打印提示，绝不硬编码密钥。
  - 每个模块可 `python -m ingestion.connectors.<name>` 直接运行（__main__ 调 .run()）。

源—连接器映射（source_id ↔ 模块，对齐 registry/metrics/*.yml 的 sources[]）：
  sec_edgar        → sec_edgar.py        （无 key，必跑通）
  stooq            → market_data.py      （无 key，RSP/SPY 必跑通）
  fred             → fred_alfred.py       （FRED_API_KEY；带 vintage）
  eia / ember / iea→ iea_eia_ember.py     （EIA_API_KEY / EMBER_API_KEY）
  bls              → bls_oes.py           （BLS v1 无 key）
  epoch_ai         → epoch_ai.py          （无 key，CC-BY CSV）
"""
