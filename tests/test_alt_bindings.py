"""策展绑定 alt_bindings.CURATED 的离线不变式检验(不触网)。

只校验"代码即真相"层的自洽:key 是真实注册表 company_id、字段形状合法、
GitHub org 全局不重复。**实核**(github/greenhouse/lever/pypi/npm 真返回 200)在生成
alt_bindings.py 时逐条完成并留档于该文件尾注,不在此重复(测试保持离线可复现)。
"""
from __future__ import annotations

from xar.ingestion.registry import COMPANIES
from xar.ontology.alt_bindings import CURATED
from xar.ontology.altdata import AltBinding, bindings

_REGISTRY_IDS = {c["id"] for c in COMPANIES}
_ALLOWED_KEYS = {"github_orgs", "ats", "pypi_packages", "npm_packages", "wiki_title"}
_ATS_PROVIDERS = {"greenhouse", "lever"}


def test_every_key_is_real_registry_company_id() -> None:
    unknown = sorted(cid for cid in CURATED if cid not in _REGISTRY_IDS)
    assert not unknown, f"CURATED keys not in registry COMPANIES: {unknown}"


def test_no_unknown_fields() -> None:
    for cid, spec in CURATED.items():
        extra = set(spec) - _ALLOWED_KEYS
        assert not extra, f"{cid}: unexpected field(s) {extra}"


def test_every_entry_yields_at_least_one_signal_field() -> None:
    # 每条策展至少要有一个真正能派生 alt 信号的字段(否则该条毫无意义)。
    signal_fields = ("github_orgs", "ats", "pypi_packages", "npm_packages")
    for cid, spec in CURATED.items():
        assert any(spec.get(f) for f in signal_fields), f"{cid}: no signal-bearing field"


def test_package_and_org_lists_are_str_sequences() -> None:
    for cid, spec in CURATED.items():
        for field in ("github_orgs", "pypi_packages", "npm_packages"):
            val = spec.get(field)
            if val is None:
                continue
            assert isinstance(val, list), f"{cid}.{field} must be a list, got {type(val)}"
            assert val, f"{cid}.{field} must be non-empty when present"
            for item in val:
                assert isinstance(item, str) and item, f"{cid}.{field} has non-str/empty item"


def test_wiki_title_override_is_str() -> None:
    for cid, spec in CURATED.items():
        if "wiki_title" in spec:
            wt = spec["wiki_title"]
            assert isinstance(wt, str) and wt, f"{cid}.wiki_title must be non-empty str"


def test_ats_shape_is_valid_tuple() -> None:
    for cid, spec in CURATED.items():
        if "ats" not in spec:
            continue
        ats = spec["ats"]
        assert isinstance(ats, tuple), f"{cid}.ats must be a tuple, got {type(ats)}"
        assert len(ats) == 2, f"{cid}.ats must be (provider, slug), got {ats!r}"
        provider, slug = ats
        assert provider in _ATS_PROVIDERS, f"{cid}.ats provider {provider!r} invalid"
        assert isinstance(slug, str) and slug, f"{cid}.ats slug must be non-empty str"


def test_no_duplicate_github_orgs_across_companies() -> None:
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for cid, spec in CURATED.items():
        for org in spec.get("github_orgs", []):
            key = org.lower()  # GitHub org 大小写不敏感,按小写查重
            if key in seen:
                dupes.append(f"{org!r} in both {seen[key]} and {cid}")
            else:
                seen[key] = cid
    assert not dupes, "duplicate github orgs: " + "; ".join(dupes)


def test_curated_flows_through_bindings() -> None:
    # 端到端:CURATED 里每个 company_id 都应在 bindings() 里产出一个有信号的 AltBinding。
    bs = bindings()
    for cid, spec in CURATED.items():
        assert cid in bs, f"{cid} curated but missing from bindings()"
        b = bs[cid]
        assert isinstance(b, AltBinding)
        assert b.signals(), f"{cid} produced no signals"
        # ats 元组应原样贯通
        if "ats" in spec:
            assert b.ats == spec["ats"], f"{cid}: ats mismatch {b.ats} != {spec['ats']}"


def test_target_coverage_is_sane() -> None:
    # 目标 ~45 家;守住一个宽松区间,防止误删/误增。
    assert 40 <= len(CURATED) <= 55, f"unexpected CURATED size {len(CURATED)}"
