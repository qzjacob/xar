"""提示词注册表 —— 让「哪一版提示词产出了这条结论」可回答。

**为什么存在**:提示词此前全是散落在各处的内联 f-string,没有版本、没有哈希。于是一条已入库的
裁决,其**输入提示词不可复原**:模板改过没有?改了哪句?这版结论是新模板还是旧模板产出的?
统统查不到。评测两版提示词孰优、或复现三个月前的一次构建,都无从谈起。

**身份 = (key, version, source_sha)**:
- `version` 人工递增,是**语义版本**——你声明「这是一次有意的改动」;
- `source_sha` 从 render 函数的**源码**算,机器自动跟踪 —— 改了字面量却忘了 bump version,
  钉扎测试(tests/test_prompt_registry.py)立刻变红。二者缺一不可:光有 version 会漏改,
  光有 sha 无法表达「这是同一个模板的第 3 版」。
- 渲染**后**的实际提示词另有 `prompt_sha`(llm._sha),逐调用记进 llm_usage / 构建快照 ——
  模板身份管「用了哪个模板」,prompt_sha 管「插值后实际发出去的是什么」。

注意:`PHANNY_DIMENSIONS` 之类被插值进提示词的常量,作为 render 的**参数**传入,
其内容因而进入渲染结果与 prompt_sha;而模板自身的 source_sha 只覆盖渲染逻辑。
"""
from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplate:
    key: str
    version: int
    render: Callable[..., str]

    def sha(self) -> str:
        return template_sha(self)


def template_sha(t: PromptTemplate) -> str:
    """render 函数源码的指纹。改了措辞就变 —— 用来抓「改了模板忘了 bump version」。"""
    try:
        src = inspect.getsource(t.render)
    except (OSError, TypeError):          # 交互式/动态定义:退化为可复现的名字指纹
        src = f"{t.render.__module__}.{t.render.__qualname__}"
    return hashlib.sha256(src.encode()).hexdigest()


# ── Phanny 提议者 ────────────────────────────────────────────────────────────────
def _phanny_proposer_system(dims: tuple[str, ...]) -> str:
    d = " / ".join(dims)
    return f"""你是对冲基金的季报事件多空交易员。给你某公司季报前的 360° dossier(含接地事实 id)。
输出一个 PhannyProposal JSON。铁律:
1. **方向只能 long 或 short**——禁止 neutral / no_trade / 弃权;证据弱就给低 conviction(可低至 1)但仍须表态;
2. **禁止把期权/结构化策略(straddle/iron condor/价差 等)当作交易观点**——只做方向性股票多空;
   期权数据(IV/skew/隐含波动)只可作为 options_structure 维度的**证据**引用;
3. evidence 里每个 id 必须逐字抄自 dossier(如 "estimate:now:eps_diluted"),严禁编造;
4. dimensions **必须覆盖全部 6 维**(不缺项、不自造别名):{d};每维 score(-2..+2)与 note 一致,
   信息缺失的维度诚实写"数据不足"而非编造;
5. conviction(1-10)与证据密度耦合:≥7 需 ≥6 个不同接地锚、asymmetry_zh 写清赔率为何不对称、
   并给 ≥1 条盘前可观测的 falsifier;
6. move_view_zh 表态 implied move 相对你预期分布是贵/便宜/合理;prob_bins 给 5 分箱
   [P(>+5%),P(+2~+5%),P(-2~+2%),P(-5~-2%),P(<-5%)] 且和≈1,e_return_pct 与之一致、符号与 direction 一致;
7. **不要输出 size**(size 由系统按 conviction/赔率/波动确定性计算)。"""


def _phanny_proposer_user(as_of: str, dossier_text: str, extra: str = "") -> str:
    return f"为下述公司生成季报多空 PhannyProposal(as_of={as_of}):\n\n{dossier_text}{extra}"


def _phanny_retry_suffix(problems: list[str]) -> str:
    if not problems:
        return ""
    return "\n\n上一稿违规,必须修正:\n- " + "\n- ".join(problems)


# ── Phanny 反方 critic ──────────────────────────────────────────────────────────
def _phanny_critic_system() -> str:
    return """你是季报事件多空交易的**反方 critic**。给你 dossier 与一份 PhannyProposal(某方向 + conviction)。
你的职责是构建**最强反方**:攻击最弱的维度、列举证伪证据、提出替代叙事。铁律:
- attack_zh 里每条论据尽量引用 dossier 的接地 id;禁止空喊;
- direction_vote ∈ {agree, disagree, abstain}:证据不足或 dossier 太薄(事实<4)→ abstain;真同意才 agree;
  **禁止反射式同意**——若 agree 也要在 attack_zh 指出至少一个残留风险;
- conviction_delta(-2..+2)、size_delta(-3..+3)给你认为应调整的方向与幅度(signed);
- rebuttal_zh:即便你反对,也把原方向的最强钢人版写出来(供裁决权衡)。"""


def _phanny_critic_user(cid: str, dossier_text: str, direction: str, conviction,
                        dims_text: str, asymmetry: str) -> str:
    return (f"公司 {cid} · dossier(接地事实):\n{dossier_text}\n\n"
            f"待挑战的 PhannyProposal:方向={direction} conviction={conviction}\n"
            f"维度:\n{dims_text}\n赔率不对称:{asymmetry or '(未给)'}\n"
            f"给出你的 signed-Δ 反方 CriticVote。")


def _phanny_rebut_user(cid: str, dossier_text: str, direction: str, conviction,
                       votes_text: str) -> str:
    return (f"公司 {cid} · dossier:\n{dossier_text}\n\n"
            f"你上一稿:方向={direction} conviction={conviction}。\n"
            f"多位异厂商 critic 的反方意见:\n{votes_text}\n\n"
            f"据此**修正并重出一个完整 PhannyProposal**(仍六维齐全、仍 long/short、evidence 接地)。"
            f"若被说服则改方向/降信念;若能反驳则维持并强化 asymmetry_zh;"
            f"**严禁仅靠降低 conviction 来平息分歧**——要么补强证据维持,要么因证据真的转向而改判。")


REGISTRY: dict[str, PromptTemplate] = {
    t.key: t for t in (
        PromptTemplate("phanny.proposer.system", 1, _phanny_proposer_system),
        PromptTemplate("phanny.proposer.user", 1, _phanny_proposer_user),
        PromptTemplate("phanny.proposer.retry", 1, _phanny_retry_suffix),
        PromptTemplate("phanny.critic.system", 1, _phanny_critic_system),
        PromptTemplate("phanny.critic.user", 1, _phanny_critic_user),
        PromptTemplate("phanny.rebut.user", 1, _phanny_rebut_user),
    )
}


def get(key: str) -> PromptTemplate:
    return REGISTRY[key]


def render(key: str, *args, **kwargs) -> tuple[str, str, int]:
    """渲染一个模板 → (文本, key, version)。调用方把 (key, version) 记进构建快照,
    回放时按同一 (key, version) 重渲染并比对 prompt_sha —— 不一致即模板已漂移。"""
    t = REGISTRY[key]
    return t.render(*args, **kwargs), t.key, t.version


def manifest() -> dict[str, dict]:
    """全表 {key: {version, sha}} —— 钉扎测试与构建快照的版本清单。"""
    return {k: {"version": t.version, "sha": t.sha()} for k, t in sorted(REGISTRY.items())}
