"""Phanny —— 与 Chathy / Andy / Genny / Fenny 平级的第五大模块(顶栏处 Genny 之后)。

对 Genny 覆盖库内选中公司(PHANNY_UNIVERSE,期权流动性名单),就**下一次季报**产出
earnings trade 交易观点:**方向(仅 long/short)· conviction 1-10(整本 book 呈正态)·
size 组合 1-15%**。经**六维推理**(基本/技术/资金/情绪/期权结构/概率赔率)+ **不同厂商 LLM
反方持续 debate 至收敛**得出;组合级正态由**证据真实分化涌现**——不达标只补数据/重辩,
**绝不为凑钟形而压低 conviction**(distribution.convergence_integrity 守卫)。

复用平台既有底座:research.earnings.dossier_earnings(接地事实)、models.llm(pinned 多厂商 +
reasoning_effort=high)、storage.db。独立表 `phanny_verdicts`,与 ET 尺度隔离。
"""
