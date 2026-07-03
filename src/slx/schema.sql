-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  Silicon-Index 数据库 schema —— 金融机构工业级、双时态、理论本体焊死       ║
-- ║  设计第一性原则：一条指标不绑定 theory_anchor + hardness +                  ║
-- ║  falsification_condition 就不允许入库（schema 级约束，而非文化倡议）。       ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- [XAR vendor local-mod] 上游此处为 CREATE EXTENSION timescaledb —— XAR 共享 Postgres
-- （pgvector/pg_trgm）不装 TimescaleDB；43 指标量级下普通表 + idx_obs_pit 足够。
-- 全部对象落入专用 schema `slx`（由 slx.db.connect 的 search_path=slx,public 保证）。

-- ── 受控词表（enum）─────────────────────────────────────────────────────────
-- 三层证据硬度：物理/会计事实 → 逻辑推论 → 待识别假说 → 承重墙不可量化项
DO $$ BEGIN
    CREATE TYPE hardness_enum AS ENUM ('hard','medium','soft','wall');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 来源等级：官方一级 → 公开可信聚合 → 厂商估算 → 派生 → 新闻
DO $$ BEGIN
    CREATE TYPE source_grade_enum AS ENUM
        ('A_official','B_public_curated','C_vendor_estimate','D_derived','E_press');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 稀缺位移后的绑定变量
DO $$ BEGIN
    CREATE TYPE scarcity_enum AS ENUM
        ('cognition','energy','compute','data','trust','legitimacy','originality','positionality');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 软件相 / 原子相 / 混合（A5 双相动力学）
DO $$ BEGIN
    CREATE TYPE phase_enum AS ENUM ('software','atom','mixed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 登记簿断言状态
DO $$ BEGIN
    CREATE TYPE claim_status_enum AS ENUM
        ('open','fixation_triggered','falsified','expired','inconclusive');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── 通用：updated_at 自动维护 ────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION fn_set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;

-- ════════════════════════════════════════════════════════════════════════════
-- 1) 理论锚点：A1–A8 + 两条元定律（受控维度表，是注册表的"外键真值"）
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS theory_anchor (
    anchor_key            text PRIMARY KEY,          -- 'A3','A5','META_migration','META_conservation'
    title                 text NOT NULL,
    industrial_assumption text,                      -- 工业时代假设（原文）
    silicon_restatement   text,                      -- 硅基重述（原文）
    verdict               text                       -- 幸存须重标 | 坍塌 | 改写
);

-- ════════════════════════════════════════════════════════════════════════════
-- 2) 指标注册表（SSOT）：理论本体 ↔ 数据模型的咬合点
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS metric_registry (
    metric_key            text PRIMARY KEY,          -- 'cost.intelligence.inference_price_per_mtok'
    display_name_zh       text NOT NULL,
    family                text NOT NULL,
    -- ===== 理论本体绑定字段（差异化内核）=====
    theory_anchor         text[]  NOT NULL,          -- 可多锚；成员须在 theory_anchor 表（见触发器）
    binding_scarcity      scarcity_enum,
    phase                 phase_enum,
    mechanism             text NOT NULL,             -- 机制一句话：为什么该数会动
    hardness              hardness_enum NOT NULL,
    identification_strategy text,                    -- soft 必填（见 CHECK）
    falsification_condition text,
    decision_window       text,                      -- '2-3y' | '10-15y' | 'testable_now'
    source_grade          source_grade_enum NOT NULL,
    caveat                text,                       -- 认识论限度注记（口径/时点/反身性）
    is_quantifiable       boolean NOT NULL DEFAULT true,   -- wall 项=false，value 永远 NULL
    -- ===== 计量与状态 =====
    unit                  text,
    geo_scope             text,
    status                text NOT NULL DEFAULT 'active',  -- active | candidate | deprecated
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    -- 纪律 1：绝不把横截面相关当因果回报 —— soft 指标必须声明识别策略
    CONSTRAINT chk_soft_identified
        CHECK (hardness <> 'soft' OR identification_strategy IS NOT NULL),
    -- 纪律 2：非空理论锚点
    CONSTRAINT chk_anchor_nonempty
        CHECK (array_length(theory_anchor, 1) >= 1)
);

DROP TRIGGER IF EXISTS trg_metric_updated_at ON metric_registry;
CREATE TRIGGER trg_metric_updated_at BEFORE UPDATE ON metric_registry
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- 数组级"外键"：theory_anchor 每个元素都须在受控词表内（A4 公理校验落到 schema）
CREATE OR REPLACE FUNCTION fn_metric_anchor_fk() RETURNS trigger AS $$
DECLARE a text;
BEGIN
    FOREACH a IN ARRAY NEW.theory_anchor LOOP
        IF NOT EXISTS (SELECT 1 FROM theory_anchor WHERE anchor_key = a) THEN
            RAISE EXCEPTION 'metric %: theory_anchor "%" 不在受控词表内', NEW.metric_key, a;
        END IF;
    END LOOP;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_metric_anchor_fk ON metric_registry;
CREATE TRIGGER trg_metric_anchor_fk BEFORE INSERT OR UPDATE ON metric_registry
    FOR EACH ROW EXECUTE FUNCTION fn_metric_anchor_fk();

-- 一条指标可由多个数据源喂养（口径互不调和时尤其关键：以区间而非点估表达）
CREATE TABLE IF NOT EXISTS metric_source (
    metric_key     text NOT NULL REFERENCES metric_registry(metric_key) ON DELETE CASCADE,
    source_id      text NOT NULL,                    -- 'epoch_ai','fred','sec_edgar','iea'...
    series_id      text NOT NULL DEFAULT '',         -- 源侧序列号
    source_grade   source_grade_enum,
    ingest_cadence text,                             -- 'monthly','quarterly','event','daily'
    vintage_aware  boolean NOT NULL DEFAULT false,   -- 该源是否提供 vintage（ALFRED=true）
    PRIMARY KEY (metric_key, source_id, series_id)
);

-- ════════════════════════════════════════════════════════════════════════════
-- 3) 双时态事实表（金融级核心 —— 防回测前视偏差）
--    valid_time     : 值"在现实里属于哪一天"（经济意义上的日期）
--    knowledge_time : transaction-time，我们"何时得知"该值（前视防护命门）
--    vintage_date   : ALFRED 口径，哪一版发布；无 vintage 用哨兵 '0001-01-01'
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS observation (
    metric_key     text        NOT NULL REFERENCES metric_registry(metric_key) ON DELETE CASCADE,
    source_id      text        NOT NULL,
    value          numeric,                          -- wall 不可量化项可为 NULL
    value_low      numeric,                          -- 口径互不调和时的区间下界
    value_high     numeric,                          -- 区间上界
    unit           text,
    valid_time     timestamptz NOT NULL,
    knowledge_time timestamptz NOT NULL,
    vintage_date   date        NOT NULL DEFAULT DATE '0001-01-01',  -- 哨兵=无独立 vintage
    snapshot_hash  text,                             -- 原始拉取载荷内容 hash（可复现）
    ingest_run_id  uuid,                             -- 指向 audit_log.ingest_run_id
    PRIMARY KEY (metric_key, source_id, valid_time, knowledge_time, vintage_date)
);

-- [XAR vendor local-mod] 上游此处将 observation 建为 TimescaleDB 超表：
--   SELECT create_hypertable('observation','valid_time',chunk_time_interval=>'365 days',if_not_exists=>TRUE);
-- 共享 Postgres 无 timescale 扩展 → 保留为普通表（PK + idx_obs_pit 覆盖 PIT 查询）。

-- point-in-time 查询加速：给定 metric + as_of，取每个 valid_time 上 knowledge_time 最新行
CREATE INDEX IF NOT EXISTS idx_obs_pit
    ON observation (metric_key, valid_time, knowledge_time DESC);

-- ── 便捷视图（注意：这是"当前最佳估计"，**严禁**用于回测/登记簿判定）──────────
-- 回测/登记簿判定必须显式带 knowledge_time <= :as_of 谓词（见 dbt point_in_time）。
CREATE OR REPLACE VIEW v_observation_current AS
SELECT DISTINCT ON (metric_key, source_id, valid_time)
       metric_key, source_id, value, value_low, value_high, unit,
       valid_time, knowledge_time, vintage_date
FROM observation
ORDER BY metric_key, source_id, valid_time, knowledge_time DESC;

-- ════════════════════════════════════════════════════════════════════════════
-- 3b) 双时态微观面板（Phase 2.2 识别引擎的原料）
--     DID / 个体固定效应需要 unit×period 微观观测，标量 observation 不够。
--     同样双时态：knowledge_time 让识别本身也 point-in-time 正确（只用那天有的面板）。
--     与 ingestion/identification_panels.PANEL_TABLE_DDL 同义（后者保证已建库幂等可用）。
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS panel_observation (
    panel_key      text        NOT NULL,                 -- 它所识别的 base metric_key
    unit_id        text        NOT NULL,                 -- 个体/职类单元
    period         date        NOT NULL,                 -- 离散时间索引
    treated        boolean     NOT NULL,                 -- DID 处理组 / within 采用者
    post           boolean     NOT NULL DEFAULT false,   -- DID 处理后期
    regressor      numeric     NOT NULL DEFAULT 0,       -- within 时变自变量（本期是否已采用 AI 技能）
    outcome        numeric     NOT NULL,
    covariates     jsonb       NOT NULL DEFAULT '{}',    -- 透明记录的时间维混淆（被 time FE 吸收）
    valid_time     timestamptz NOT NULL,
    knowledge_time timestamptz NOT NULL,                 -- 双时态：那天能知道这条面板观测吗
    ingest_run_id  uuid,
    snapshot_hash  text,
    PRIMARY KEY (panel_key, unit_id, period, knowledge_time)
);
CREATE INDEX IF NOT EXISTS idx_panel_pit
    ON panel_observation (panel_key, period, knowledge_time DESC);

-- ════════════════════════════════════════════════════════════════════════════
-- 4) 过度宣称登记簿（差异化内核之二：活监控）
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS overclaim_registry (
    claim_key        text PRIMARY KEY,
    claim_text_zh    text NOT NULL,
    related_metrics  text[] NOT NULL DEFAULT '{}',   -- 成员须在 metric_registry（见触发器）
    hardness         hardness_enum,
    decision_window  text NOT NULL,                  -- '1-2y','2-3y','10-15y','1-2q'
    window_start     date NOT NULL,
    fixation_rule    text NOT NULL,                  -- K"固化"观测（断言成立）
    falsify_rule     text NOT NULL,                  -- K"收敛/证伪"观测（断言被推翻）
    status           claim_status_enum NOT NULL DEFAULT 'open',
    last_evaluated   timestamptz,
    evidence_snapshot jsonb,
    owner            text NOT NULL DEFAULT 'Andi',
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- related_metrics 引用完整性（每个元素须为已登记指标）
CREATE OR REPLACE FUNCTION fn_overclaim_metric_fk() RETURNS trigger AS $$
DECLARE m text;
BEGIN
    FOREACH m IN ARRAY NEW.related_metrics LOOP
        IF NOT EXISTS (SELECT 1 FROM metric_registry WHERE metric_key = m) THEN
            RAISE EXCEPTION 'overclaim %: related_metric "%" 未登记', NEW.claim_key, m;
        END IF;
    END LOOP;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_overclaim_metric_fk ON overclaim_registry;
CREATE TRIGGER trg_overclaim_metric_fk BEFORE INSERT OR UPDATE ON overclaim_registry
    FOR EACH ROW EXECUTE FUNCTION fn_overclaim_metric_fk();

-- 每次评估留痕（趋势可视 + 可复现的证据快照）
CREATE TABLE IF NOT EXISTS overclaim_eval_log (
    claim_key       text NOT NULL REFERENCES overclaim_registry(claim_key) ON DELETE CASCADE,
    evaluated_at    timestamptz NOT NULL DEFAULT now(),
    as_of_date      date NOT NULL,
    verdict         claim_status_enum NOT NULL,
    metric_readings jsonb,
    triggered       boolean NOT NULL DEFAULT false,
    PRIMARY KEY (claim_key, evaluated_at)
);

-- ════════════════════════════════════════════════════════════════════════════
-- 5) 审计日志（溯源 / 可复现根基）：每次摄取一行，observation.ingest_run_id 指向它
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS audit_log (
    ingest_run_id  uuid PRIMARY KEY,
    source_id      text NOT NULL,
    connector      text NOT NULL,                    -- 连接器模块名
    git_commit     text,                             -- 拉取时的代码版本
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    rows_written   integer,
    payload_hash   text,                             -- 原始响应载荷 hash
    request_meta   jsonb,                            -- 端点/参数/口径/时点
    status         text NOT NULL DEFAULT 'running',  -- running | ok | error
    error          text
);
