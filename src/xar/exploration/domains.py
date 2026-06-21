"""Exploration domain registry — the "sections" of the frontier module.

Each domain mirrors a Research-Portal theme: a coherent frontier of knowledge with
its own arXiv categories, expert voices (X handles), and search terms. `ai` is the
first section and the one wired end-to-end. Order here is the display order.
"""
from __future__ import annotations

# id -> definition. `arxiv_cats` drives preprint ingestion; `handles`/`terms`
# drive the expert-voice (X) pull; non-arXiv domains (psych/politics) lean on X.
DOMAINS: list[dict] = [
    {
        "id": "ai",
        "name": "Artificial Intelligence",
        "nameCn": "人工智能前沿",
        "icon": "brain",
        "blurb": "Frontier of machine intelligence: agents, reasoning, world models, "
                 "post-training, efficiency, embodiment.",
        "blurbCn": "机器智能的最前沿：智能体、推理、世界模型、后训练、效率与具身。",
        "arxiv_cats": ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.MA", "stat.ML"],
        "handles": ["ylecun", "karpathy", "JeffDean", "drfeifei", "_jasonwei",
                    "DrJimFan", "hardmaru", "polynoamial", "OriolVinyalsML", "demishassabis"],
        "terms": ['"world model"', '"test-time compute"', '"reasoning model"',
                  '"AI agent"', '"mixture of experts"', '"reinforcement learning"'],
    },
    {
        "id": "physics",
        "name": "Physics",
        "nameCn": "物理学",
        "icon": "atom",
        "blurb": "Quantum information, condensed matter, high-energy theory, gravitation.",
        "blurbCn": "量子信息、凝聚态、高能理论与引力。",
        "arxiv_cats": ["quant-ph", "cond-mat.str-el", "hep-th", "gr-qc", "physics.app-ph"],
        "handles": ["seanmcarroll", "PhysicsToday", "preskill", "QuantaMagazine"],
        "terms": ['"quantum error correction"', '"quantum advantage"', "superconductivity",
                  '"topological"'],
    },
    {
        "id": "math",
        "name": "Mathematics",
        "nameCn": "数学",
        "icon": "sigma",
        "blurb": "Number theory, geometry, combinatorics, probability, optimization; "
                 "and AI-for-math (proof automation).",
        "blurbCn": "数论、几何、组合、概率、最优化，以及 AI 辅助证明。",
        "arxiv_cats": ["math.AG", "math.NT", "math.CO", "math.PR", "math.OC"],
        "handles": ["QuantaMagazine", "ProfTerrytao", "littmath"],
        "terms": ['"automated theorem proving"', '"Lean proof"', "conjecture"],
    },
    {
        "id": "cs_systems",
        "name": "Computing & Systems",
        "nameCn": "计算与系统",
        "icon": "cpu",
        "blurb": "Architecture, distributed systems, security/cryptography, algorithms "
                 "— the substrate compute runs on.",
        "blurbCn": "体系结构、分布式系统、安全密码学与算法——算力的底座。",
        "arxiv_cats": ["cs.DC", "cs.AR", "cs.OS", "cs.DS", "cs.CR"],
        "handles": ["matei_zaharia", "danluu", "AndrewYNg"],
        "terms": ['"distributed training"', '"post-quantum"', '"accelerator"', '"inference"'],
    },
    {
        "id": "neuro",
        "name": "Neuro & Cognition",
        "nameCn": "神经与认知",
        "icon": "activity",
        "blurb": "Computational neuroscience, cognition, brain–computer interfaces; "
                 "the biology that grounds (and competes with) artificial minds.",
        "blurbCn": "计算神经科学、认知、脑机接口——支撑并对照人工智能的生物基础。",
        "arxiv_cats": ["q-bio.NC"],
        "handles": ["KordingLab", "TonyZador", "Neuro_Skeptic"],
        "terms": ['"brain-computer interface"', '"neural code"', '"connectome"'],
    },
    {
        "id": "complex",
        "name": "Complex Systems & Society",
        "nameCn": "复杂系统与社会",
        "icon": "globe",
        "blurb": "Econophysics, networks, collective behavior, geopolitics of technology "
                 "— the long-horizon, directional forces shaping the frontier.",
        "blurbCn": "经济物理、网络、群体行为与科技地缘——塑造前沿的长期方向性力量。",
        "arxiv_cats": ["physics.soc-ph", "econ.GN", "nlin.AO"],
        "handles": ["business", "FT", "tylercowen"],
        "terms": ['"compute governance"', '"export controls"', '"collective intelligence"'],
    },
]

DOMAINS_BY_ID: dict[str, dict] = {d["id"]: d for d in DOMAINS}


def domain_by_id(domain_id: str) -> dict | None:
    return DOMAINS_BY_ID.get(domain_id)
