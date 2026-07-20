# 微信公众号「全网」高信噪比资讯检索系统 —— Fetchy WeChat Discovery

买方投研里微信公众号是唯一的中文一手产业评论源。现状只覆盖了一半:抓取侧只轮询**手动
订阅**的号(we-mp-rss + `WERSS_FEEDS`),你没订阅的高价值号进不来;去噪侧已很强(`mining/
triage.py`)。本系统补上「全网发现」这一半 —— 用**本体驱动搜索 + 逐篇正文抓取**近似全网,
发现文档全部灌进**现有 triage**,零改动复用去噪/门控/抽取全链路。

微信是围墙花园,任意全网爬取不可能干净实现。本方案用**混合漏斗**逼近,并把最脆弱的反爬
一环隔离在外部自托管服务里、**默认关闭**、可随时降级回纯订阅。

---

## 一、混合漏斗架构(已落地 Phase 1)

```
① 查询生成(本体种子, 按天轮转)
   → ② wechat_search.search()  [全网, 自托管搜索服务]
   → ③ 去重 vs documents + 抓正文(复用 news._fetch/_extract)+ save(source='wechat')
   → [现有 triage 去噪 → kg_extract]
   → ④ 高产号 roster.register 晋升为策展订阅(每日上限内)  ──┐
        ↑______________ 脆弱搜索命中 → 耐久订阅的自愈收敛 _____│
```

| 段 | 实现 | 复用 |
|---|---|---|
| ① 查询生成 | `ingestion/wechat_discover._queries()` 公司中文别名 + 主题/路线中文词;`_slice_for_today()` 按 UTC 日序无状态轮转,每日跑一片(`wechat_discover_queries_per_run`) | `ontology/cn_routing`(8 主题 + 33 路线词表)、`ingestion/registry.COMPANIES` 别名 |
| ② 全网搜索 | `ingestion/wechat_search.search(q, since_days)` 薄客户端 → 归一化 `{title,url,account,gh_id,date,snippet}`,只留 mp.weixin.qq.com 链接;失败 WARN 返回 [] | 后端无关适配层,换后端只改 `_endpoint()`/`_normalize()` |
| ③ 抓取+落库 | URL 先查 `documents` 去重 → `news._fetch` GET → `news._extract`(trafilatura)→ 正文短于 `wechat_discover_min_chars` 跳过 → `save(Doc(source='wechat', doc_type='mp_search', permission='grey', meta={via:'discover',account,gh_id,query}))` | `ingestion/news._fetch/_extract`、`ingestion/base.Doc/save/polite`、`wechat._alias_index/_link_company/_parse_date` |
| ④ 去噪 | 无改动:`source='wechat'` 自动进 `triage.wechat_pending_clause()` 门控 → `WECHAT_TRIAGE` 打分 → 高分进 kg_extract | `mining/triage.py` 全链、`models/router.WECHAT_TRIAGE` |
| ④ 晋升 | `mining/wechat_promote.promote_candidates()` 按 gh_id 聚合 triage 产出 → 够格号(`≥min_articles` 且 `keep_rate≥min_keep_rate`)在 `max_per_day` 内订阅 + `roster.register` 进策展名册 | `mining/roster.register()`(既有 feed 名册管理) |

**候选与名册分表**(关键设计):策展订阅名册 `wechat_accounts`(feed_id 键,`mining/roster.py`
既有)是现成的稳定轮询源;发现候选订阅前**没有 feed_id**,故单列 `wechat_discovered`(gh_id
键)记 triage 产出统计。晋升 = 订阅拿到 feed_id → `roster.register` 落名册 → 稳定轮询接管。

### 落地文件
- 新:`ingestion/wechat_search.py`、`ingestion/wechat_discover.py`、`mining/wechat_promote.py`
- 新表:`wechat_discovered`(schema.sql;`wechat_accounts` 是既有名册,勿混)
- 配置:`config.py` `wechat_search_*` / `wechat_discover_*` / `wechat_promote_*`(镜像 `werss_*`)
- 接线:`orchestration/daily.py`(wechat 段加 discover + promote)、`cli.py` `ingest-wechat-discover`、
  `api/ops.py` fetchy `wechatDiscover` 观测、`api/app.py` `POST /api/ingest/wechat-discover`
- 测试:`tests/test_wechat_discover.py`(11 项全绿:查询/轮转/去重/短文跳过/落库口径/归一化/晋升门)

---

## 二、搜索后端契约(spike 定型点)

XAR 只依赖一个 HTTP 接口(`wechat_search._endpoint()` 默认 `GET {base}/api/search`):

- **请求**:`GET {WECHAT_SEARCH_BASE_URL}/api/search?q=<关键词>&days=<N>&limit=<N>`(带 `q` 与
  `keyword` 两个键名兼容不同后端;可选 `Authorization: Bearer <WECHAT_SEARCH_API_TOKEN>`)。
- **响应**:JSON,条目列表位于 `items|results|list|articles|data(.list)` 任一;每条含
  `url/link`(mp.weixin.qq.com 永久链接,必需)、`title`、`account/nickname`、`gh_id/biz`、
  `date/publish_time`、`snippet/digest` 任一同义键即可(客户端已做多键容错)。

**第一阶段自托管后端候选**(构建期 spike 取「有稳定 HTTP 搜索端点 + 在维护」者):
1. `tmwgsicp/wechat-download-api` —— 开源,文章获取 + RSS API + **IP 代理池反风控** + MCP。首选。
2. `fancyboi999/weixin_search_mcp` —— 公众号搜索 + 正文获取 MCP。备选。
3. we-mp-rss 内置关键词搜索 —— 已在运行,零新服务;偏账号级搜索,兜底。

后端返回体若与上面契约不同,**只改 `wechat_search._endpoint()` / `_normalize()` 一处**。

---

## 三、部署 runbook(谷时窗;默认关,零扰动)

1. **部署搜索服务**(见 `~/JakeOS/hardware-solutions/apply-minis-wechat-search.sh`):选定候选
   → docker 容器,**绑 172.17.0.1:<port>**(容器可达、LAN 不可达,镜像 ollama/we-mp-rss),
   走 Clash 代理。纯网络负载、无 GPU/ml.slice。首次需**扫码登录 + 会话持久化**(挂卷)。
2. **验证搜索端点**:`curl 'http://172.17.0.1:<port>/api/search?q=光模块&days=14'` 返回文章列表
   (含 mp.weixin.qq.com url)。若响应形态不同 → 调 `wechat_search._normalize()`。
3. **点亮发现**:`.env` 加
   ```
   WECHAT_SEARCH_BASE_URL=http://172.17.0.1:<port>
   XAR_WECHAT_DISCOVER_ENABLED=true
   ```
   → `docker compose up -d`(**recreate 才重读 .env,restart 不行**)。
4. **冒烟**:`xar ingest-wechat-discover --dry-run-promote` → 看落库 `source='wechat' doc_type=
   'mp_search'`;下个 glm_worker 周期 triage 写 `triage_score`(`xar` 侧 `triage.stats()` 看
   keep_rate);高分进 kg_extract(`route kg_extract` 日志)。
5. **观测**:Jarvy Fetchy 页 `wechatDiscover`(开关/发现数/晋升漏斗)。晋升候选可先
   `--dry-run-promote` 审阅排序再放开自动订阅。
6. **回滚**:`.env` 去掉 `XAR_WECHAT_DISCOVER_ENABLED` → `up -d`;发现即 no-op,纯订阅 we-mp-rss
   与 triage 全不受影响。

---

## 四、未来开发计划(本期不做)

- **F1 仅付费 API 发现方案评估**:newrank 新榜 数据 API(api.newrank.cn)/ 清博 做发现 ——
  可靠但 ¥ 成本、正文覆盖可能不全;与自建搜索做覆盖/成本/可靠性对比,决定是否切换/并用。
- **F2 付费热度信号入 triage**:NRI 阅读量/点赞/转发 补微信「无阅读数」洞(`triage.py` 注释
  明确的缺口)—— 替代/增强当前 `novelty×specificity` 的「低传播高价值救回」。
- **F3 Sogou 直爬兜底**:自建搜索覆盖不足时,评估 WechatSogou 式直爬(cookie 轮换 + 验证码
  + 代理)作为发现补充 —— 脆弱,只隔离在外部服务里,绝不进 XAR 主代码库。

---

## 五、风险与纪律

1. **搜索后端未证实**:Phase 1 服务的稳定端点/维护状态需 spike 确认;薄适配层 + 默认关 +
   纯订阅降级线兜底。
2. **反爬脆弱**:搜索一环易断 —— 隔离外部服务、默认关、查询按天轮转限速、失败 WARN 不炸。
3. **自动晋升风险**:阈值(keep_rate + 最小篇数)+ 每日上限 + `wechat_discovered` 审计,防垃圾
   号灌进策展名册 / 防打爆 we-mp-rss 会话限流。首放建议 `--dry-run-promote` 观察一两轮。
4. **posture 不变**:`permission='grey'` + 存事实 + 引用 URL,不转载原文 —— 与既有 wechat/news
   一致,无治理变更;搜索服务绑 172.17.0.1,绝不 0.0.0.0/LAN。
