# LOCAL_LLM_PLAN — glmworker 本地 LLM 运行位:后续开发计划(Phase 5)

> **建档 2026-07-19/20**。Phase 3(本地优先接线)与 Phase 4(赛马换代 qwen3-14b)已收官——
> 现状与机制的权威描述在 `DESIGN.md §6.1「本地 ollama 条目」`;主机侧治理在
> `~/JakeOS/hardware-solutions/minis-算力调度方案.md §9`(赛果表 §9.6、踩坑 §9.7);
> 选型方法论沉淀 `~/JakeOS/memory/ml/`;评测数据 `~/Project/XAR/bench/phase4/`(仓外)。
> 本文档收录**收官时遗留的三项后续工作**,按优先级排列。

## 现状快照(2026-07-19 切换后 6h 实证)

- 钉扎链 `('qwen3-14b-local', 'glm-5.2-sub', 'glm-4.6-sub')`,kg_extract 云端占比 **0.0%**;
  本地 out 均值 1499 tok(glm4 时代 340 的 4.4×,欠抽取修复);当日抽取 1745 篇为 7d 最高。
- `mlrun --exclusive` 抢占演练:真停 5s 真恢复;VRAM 11.17G(共存红线 11.2G 实测口径)。
- 回滚位在位:`glm4-local` spec ACTIVE + `glm4-xar`/`glm4:9b-chat-q4_K_M` tag 未删;
  回滚 = `.env` 改 `XAR_GLM_WORKER_LOCAL_MODEL=glm4-local` + `docker compose up -d glmworker`。
- 监控口径:`minis:~/bin/soak-check.sh [hours]`(云端占比/out 均值/日抽取量/ml.slice/VRAM/OOM)。

---

## P0 — glmworker 容器内存增长循环:根因排查与修复

**现象**(2026-07-19 取证,内核 journal):`xar` worker 进程 anon-RSS 约每 3–4 小时增长到
容器 `mem_limit: 5g`,被 memcg OOM kill,docker `restart: unless-stopped` 自动重启。
48h 内 3 次(00:23 / 04:26 / 17:54),签名一致(anon ~5.22G)。**切换前已存在,与 Phase 3/4 无关**;
当前靠 docker 自愈兜底,无生产事故,但每次重杀丢一轮周期上下文、且是潜在的隐性劣化源。

**排查方向**(按嫌疑度):
1. 常驻循环的**累积性缓存**——`kg/resolve` 的实体缓存、`graphrag`/嵌入相关对象、psycopg
   连接/游标残留、`documents` 大文本对象未释放;
2. `parse_pending` / fastembed(容器内是否意外初始化了本应宿主侧的嵌入路径);
3. LiteLLM 客户端对象累积(每候选/每调用是否复建 client);
4. glibc malloc arena 碎片(容器多线程 + 长驻;可试 `MALLOC_ARENA_MAX=2` 低成本排除)。

**做法**:容器内加周期性 RSS 自报(worker 每 N 周期 log `resource.getrusage`/`tracemalloc` top);
或宿主 `docker stats` 采样定位增长斜率与阶段相关性(pull/parse/extract 哪一段涨)。
**验收**:worker 连续 ≥48h 无 memcg kill(`journalctl -k | grep "Memory cgroup out of memory"` 零新增),
或有意识地裁定「接受自愈循环」并把 restart 语义与周期状态恢复做成显式设计(文档化)。

## P1 — 7 天 soak(至 2026-07-26)与通过后的收尾清理

**soak 口径**(每日一次 `soak-check.sh 24`):
- kg_extract 云端占比 ≤5%(回落信号;本地失败不记账,云占比即信号);
- 本地 out 均值维持 ≫340(欠抽取不回潮);日 `kg_extracted` 文档数不低于 7d 均值;
- `dropped_ungrounded` 比例不恶化;VRAM ≤11.2G;ml.slice 无 oomd kill。

**通过后的清理**(一次提交 + 一次 minis 操作):
1. `models/registry.py`:三个赛马败者 spec(`qwen35-local`/`qwen3-local`/`glm4-0414-local`)删除;
   `glm4-local` → `Status.DEPRECATED`(**先查** `glm_worker_state["fetchy"].model` 未显式引用它);
   同步修 `tests/test_glm_worker.py::test_candidate_specs_registered`。
2. minis:`ollama rm qwen35-xar qwen3-xar glm4-0414-xar qwen3.5:9b qwen3:8b-q4_K_M
   milkey/GLM-4-9B-0414:q4_K_M glm4-xar glm4:9b-chat-q4_K_M`(释放 ~42G;
   保留 `qwen3-14b-xar` + 其基座 `qwen3:14b-q4_K_M`)。
3. vault:算力调度方案 §9/§10 记 soak 结论;`glm4-local` 退役后回滚位改为「重拉 tag + 复活 spec」。

**若 soak 失败**(任一口径连续 2 天不达标):回滚至 `glm4-local`,携数据复盘
(bench/phase4 黄金集可直接重跑复现),再议 8B 档或云回落策略。

## P2 — ml.slice 页缓存顶 High 的观察项(条件触发)

**现象**:14B 模型文件 9.3G 的 mmap 页缓存使 `ml.slice` `memory.peak` 恰触 High(10240M),
`memory.events high` 抛压计数增长——**均为可回收缓存页,oom_kill=0,推理不受影响**
(权重常驻 VRAM,页缓存只影响冷加载速度)。

**触发条件**(满足其一才行动,否则维持现状):
- 模型冷加载时长显著劣化(基线:35s 量级;可在抢占恢复后的首个本地调用延迟观察);
- `ml.slice` 出现 oomd kill 或 zram 溢出持续 >1G。

**动作**:`sudo bash ~/bin/apply-minis-phase4-model-upgrade.sh --big`(ml.slice 二切 12G/16G,
幂等含零扰动断言;caps 合计 84G ≤92G 仍不超售,真实边际压至 ~3–6G 贴 earlyoom 地板——
执行后在 `minis-内存管理方案.md §2` 落「预算 v5」)。

---

## 展望(非承诺,依据 算力调度方案 §9.6 升级路径)

- **吞吐升级**:夜批量继续增长(如 947→2000 司)且单模型串行成瓶颈时,评估 vLLM
  (`--gpu-memory-utilization ≈0.45` 锁共存预算 + 重立宿主 RAM 预算)——continuous batching
  对并发批量 2–9×;单流场景 ollama 已足。
- **下代换代**:复用 `scripts/bench_local_llm.py` 赛马 runbook(黄金集重抽 + 云锚刷新),
  候选纳入门槛:q4 权重 + 16k×2 slots q8 KV 总 VRAM ≤11.2G(共存)——更大即挂账准独占裁定。
