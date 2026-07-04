"""策展绑定 —— 软件链 top 名单的 GitHub org / ATS 招聘板 / PyPI·npm 包(逐条实核)。

被 ontology/altdata.py 的 ``bindings()`` 消费:``CURATED[company_id]`` 里的策展字段与
派生字段(台股月营收码 / Wiki 词条)合并成一家公司的 ``AltBinding``。契约(见 altdata.py):

    CURATED = {company_id: {
        "github_orgs":   [org, ...],              # api.github.com/orgs/{org} → 200
        "ats":           ("greenhouse"|"lever", slug),
        "pypi_packages": [pkg, ...],
        "npm_packages":  [pkg, ...],
        "wiki_title":    optional 覆盖,           # 省略=按注册表英文名派生
    }}

**为何只挑这些**:开发者/雇佣遥测只有在"能撬动投资论点"时才值得追——OSS 心智份额、
devtools 采用漏斗、官方招聘板的扩张/收缩先行信号。因此本表聚焦 ai_software 链的
基础设施 / 数据 / 可观测 / 安全 / devtools 名单,外加几个链上关键名(NVIDIA / Arm)。
company_id 全部取自 xar.ingestion.registry.COMPANIES(theme=ai_software,及链名)。

**实核口径**(每条尾注含实核日期;主体实核于 2026-07-04):
  - github: ``https://api.github.com/orgs/{org}`` 返回 200 且 type=Organization。
    速率受限时按契约允许改用 ``https://github.com/{org}`` HEAD(200)——本表 3 条
    (Unity-Technologies / NVIDIA / ARM-software)因 GitHub 未鉴权配额耗尽走 HEAD 复核。
  - greenhouse: ``boards-api.greenhouse.io/v1/boards/{slug}/jobs`` 200 且 jobs>0。
  - lever: ``api.lever.co/v0/postings/{slug}`` 200 且返回非空 JSON 数组。
  - pypi: ``pypi.org/pypi/{pkg}/json`` 200 且 project_urls/author 指向该公司(非社区包)。
  - npm: ``registry.npmjs.org/{pkg}`` 200 且 repository/homepage 指向该公司 org/域名。

**实核未通过 → 静默剔除(此处留档)**:
  - veev(Veeva)GitHub org ``veevasystems`` → 404;仅保留其 lever 招聘板(793 postings),
    故 veev 为"仅 ATS"绑定。
  - hubs(HubSpot)greenhouse ``hubspot`` → 200 但 jobs=0(未达"带在招职位"门槛)→ 弃 ATS,
    仅保留 GitHub/PyPI/npm。
  - team(Atlassian)lever ``atlassian`` → 200 但 0 postings → 弃 ATS,仅保留 GitHub。
  - docn(DigitalOcean)pypi ``python-digitalocean`` = 社区作者(koalalorenzo),project_urls
    不指向公司 → 弃;docn 为"仅 GitHub"绑定。
  - crm(Salesforce)pypi ``simple-salesforce`` = 社区 org → 弃(改用官方 npm ``@salesforce/core``)。
  - npm ``mongoose`` = Automattic(社区)→ 弃(mdb 用官方 ``mongodb`` / ``bson``)。

新增一条 = 加一个 company_id 键并逐字段实核——评分/健康度/前端零改动。
"""
from __future__ import annotations

# 类型仅作文档提示;bindings() 用 .get 容错读取,故用宽松 dict 标注。
CURATED: dict[str, dict] = {
    # ── 基础设施 / DevInfra ─────────────────────────────────────────────────────
    "net": {  # Cloudflare
        "github_orgs": ["cloudflare"],                    # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "cloudflare"),              # jobs=239              2026-07-04
        "npm_packages": ["wrangler", "miniflare"],        # cloudflare/workers-sdk 2026-07-04
    },
    "team": {  # Atlassian
        "github_orgs": ["atlassian"],                     # api 200 Organization  2026-07-04
    },
    "docn": {  # DigitalOcean
        "github_orgs": ["digitalocean"],                  # api 200 Organization  2026-07-04
    },
    "ntnx": {  # Nutanix
        "github_orgs": ["nutanix"],                       # api 200 Organization  2026-07-04
    },
    "frog": {  # JFrog
        "github_orgs": ["jfrog"],                         # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "jfrog"),                   # jobs=58               2026-07-04
    },
    "path": {  # UiPath
        "github_orgs": ["UiPath"],                        # api 200 Organization  2026-07-04
    },
    "gtlb": {  # GitLab
        "github_orgs": ["gitlabhq"],                      # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "gitlab"),                  # jobs=143              2026-07-04
    },
    "fsly": {  # Fastly
        "github_orgs": ["fastly"],                        # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "fastly"),                  # jobs=52               2026-07-04
        "pypi_packages": ["fastly"],                      # fastly/fastly-py      2026-07-04
        "npm_packages": ["@fastly/js-compute"],           # fastly/js-compute-runtime 2026-07-04
    },
    "dt": {  # Dynatrace
        "github_orgs": ["Dynatrace"],                     # api 200 Organization  2026-07-04
    },
    # ── 数据 / 数据库 / 流 ───────────────────────────────────────────────────────
    "orcl": {  # Oracle
        "github_orgs": ["oracle"],                        # api 200 Organization  2026-07-04
        "pypi_packages": ["oracledb"],                    # oracle/python-oracledb 2026-07-04
    },
    "snow": {  # Snowflake
        "github_orgs": ["snowflakedb"],                   # api 200 Organization  2026-07-04
        "pypi_packages": [
            "snowflake-connector-python",                 # author Snowflake, Inc 2026-07-04
            "snowflake-snowpark-python",                  # author Snowflake, Inc 2026-07-04
        ],
        "npm_packages": ["snowflake-sdk"],                # snowflakedb/…-nodejs  2026-07-04
    },
    "mdb": {  # MongoDB
        "github_orgs": ["mongodb"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "mongodb"),                 # jobs=397              2026-07-04
        "pypi_packages": ["pymongo"],                     # mongodb/mongo-python-driver 2026-07-04
        "npm_packages": ["mongodb", "bson"],              # mongodb/*             2026-07-04
    },
    "cflt": {  # Confluent
        "github_orgs": ["confluentinc"],                  # api 200 Organization  2026-07-04
        "pypi_packages": ["confluent-kafka"],             # confluentinc/…-python 2026-07-04
        "npm_packages": ["@confluentinc/kafka-javascript"],  # confluentinc/*    2026-07-04
    },
    "estc": {  # Elastic
        "github_orgs": ["elastic"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "elastic"),                 # jobs=203              2026-07-04
        "pypi_packages": ["elasticsearch", "elastic-transport"],  # elastic/*    2026-07-04
        "npm_packages": ["@elastic/elasticsearch"],       # elastic/elasticsearch-js 2026-07-04
    },
    "ibm": {  # IBM
        "github_orgs": ["IBM"],                           # api 200 Organization  2026-07-04
        "pypi_packages": ["ibm-watson"],                  # author IBM Watson     2026-07-04
    },
    "pltr": {  # Palantir
        "github_orgs": ["palantir"],                      # api 200 Organization  2026-07-04
        "ats": ("lever", "palantir"),                     # postings=275          2026-07-04
        "npm_packages": ["@blueprintjs/core"],            # palantir/blueprint    2026-07-04
    },
    # ── 可观测 ──────────────────────────────────────────────────────────────────
    "ddog": {  # Datadog
        "github_orgs": ["DataDog"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "datadog"),                 # jobs=406              2026-07-04
        "pypi_packages": ["datadog", "ddtrace"],          # DataDog/*             2026-07-04
        "npm_packages": ["dd-trace"],                     # DataDog/dd-trace-js   2026-07-04
    },
    # ── 安全 ────────────────────────────────────────────────────────────────────
    "crwd": {  # CrowdStrike
        "github_orgs": ["CrowdStrike"],                   # api 200 Organization  2026-07-04
        "pypi_packages": ["crowdstrike-falconpy"],        # CrowdStrike/falconpy  2026-07-04
    },
    "panw": {  # Palo Alto Networks
        "github_orgs": ["PaloAltoNetworks"],              # api 200 Organization  2026-07-04
    },
    "ftnt": {  # Fortinet
        "github_orgs": ["fortinet"],                      # api 200 Organization  2026-07-04
    },
    "zs": {  # Zscaler
        "github_orgs": ["zscaler"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "zscaler"),                 # jobs=320              2026-07-04
    },
    "akam": {  # Akamai
        "github_orgs": ["akamai"],                        # api 200 Organization  2026-07-04
        "pypi_packages": ["edgegrid-python"],             # akamai/AkamaiOPEN-…   2026-07-04
    },
    "rbrk": {  # Rubrik
        "github_orgs": ["rubrikinc"],                     # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "rubrik"),                  # jobs=99               2026-07-04
    },
    "cybr": {  # CyberArk
        "github_orgs": ["cyberark"],                      # api 200 Organization  2026-07-04
    },
    "tenb": {  # Tenable
        "github_orgs": ["tenable"],                       # api 200 Organization  2026-07-04
        "pypi_packages": ["pytenable"],                   # author Tenable, Inc.  2026-07-04
    },
    "okta": {  # Okta
        "github_orgs": ["okta"],                          # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "okta"),                    # jobs=361              2026-07-04
        "npm_packages": ["@okta/okta-sdk-nodejs"],        # okta/okta-sdk-nodejs  2026-07-04
    },
    # ── 生产力 / 协作 ───────────────────────────────────────────────────────────
    "msft": {  # Microsoft
        "github_orgs": ["microsoft"],                     # api 200 Organization  2026-07-04
    },
    "now": {  # ServiceNow
        "github_orgs": ["ServiceNow"],                    # api 200 Organization  2026-07-04
    },
    "zm": {  # Zoom
        "github_orgs": ["zoom"],                          # api 200 Organization  2026-07-04
    },
    "dbx": {  # Dropbox
        "github_orgs": ["dropbox"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "dropbox"),                 # jobs=51               2026-07-04
        "pypi_packages": ["dropbox"],                     # dropbox/dropbox-sdk-python 2026-07-04
        "npm_packages": ["dropbox"],                      # dropbox/dropbox-sdk-js 2026-07-04
    },
    "box": {  # Box
        "github_orgs": ["box"],                           # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "boxinc"),                  # jobs=140              2026-07-04
        "pypi_packages": ["boxsdk"],                      # box/box-python-sdk    2026-07-04
        "npm_packages": ["box-node-sdk"],                 # box/box-node-sdk      2026-07-04
    },
    "mndy": {  # monday.com
        "github_orgs": ["mondaycom"],                     # api 200 Organization  2026-07-04
        "npm_packages": ["monday-sdk-js"],                # mondaycom/monday-sdk-js 2026-07-04
    },
    "smar": {  # Smartsheet
        "github_orgs": ["smartsheet-platform"],           # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "smartsheet"),              # jobs=102              2026-07-04
    },
    # ── CRM / 营销 / 平台 ───────────────────────────────────────────────────────
    "crm": {  # Salesforce
        "github_orgs": ["salesforce"],                    # api 200 Organization  2026-07-04
        "npm_packages": ["@salesforce/core"],             # forcedotcom/sfdx-core 2026-07-04
    },
    "hubs": {  # HubSpot
        "github_orgs": ["HubSpot"],                       # api 200 Organization  2026-07-04
        "pypi_packages": ["hubspot-api-client"],          # HubSpot/hubspot-api-python 2026-07-04
        "npm_packages": ["@hubspot/api-client"],          # HubSpot/hubspot-api-nodejs 2026-07-04
    },
    "twlo": {  # Twilio
        "github_orgs": ["twilio"],                        # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "twilio"),                  # jobs=153              2026-07-04
        "pypi_packages": ["twilio"],                      # twilio/twilio-python  2026-07-04
        "npm_packages": ["twilio"],                       # twilio/twilio-node    2026-07-04
    },
    "kvyo": {  # Klaviyo
        "github_orgs": ["klaviyo"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "klaviyo"),                 # jobs=169              2026-07-04
        "pypi_packages": ["klaviyo-api"],                 # klaviyo/klaviyo-api-python 2026-07-04
    },
    "brze": {  # Braze
        "github_orgs": ["braze-inc"],                     # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "braze"),                   # jobs=229              2026-07-04
        "npm_packages": ["@braze/web-sdk"],               # braze-inc/braze-web-sdk 2026-07-04
    },
    "adbe": {  # Adobe
        "github_orgs": ["adobe"],                         # api 200 Organization  2026-07-04
        "npm_packages": ["@adobe/css-tools"],             # adobe/css-tools       2026-07-04
    },
    "sap": {  # SAP
        "github_orgs": ["SAP"],                           # api 200 Organization  2026-07-04
        "npm_packages": ["@sap/cds"],                     # homepage cap.cloud.sap 2026-07-04
    },
    "intu": {  # Intuit
        "github_orgs": ["intuit"],                        # api 200 Organization  2026-07-04
    },
    "wix": {  # Wix
        "github_orgs": ["wix"],                           # api 200 Organization  2026-07-04
    },
    # ── 垂直 SaaS / 平台 ────────────────────────────────────────────────────────
    "iot": {  # Samsara
        "github_orgs": ["samsara"],                       # api 200 Organization  2026-07-04
        "ats": ("greenhouse", "samsara"),                 # jobs=332              2026-07-04
    },
    "veev": {  # Veeva —— 仅 ATS(GitHub org 404,见模块头)
        "ats": ("lever", "veeva"),                        # postings=793          2026-07-04
    },
    "u_us_u": {  # Unity Software
        "github_orgs": ["Unity-Technologies"],            # HEAD 200(配额耗尽复核) 2026-07-04
    },
    # ── 链上关键名(非 ai_software,但开发者遥测强)──────────────────────────────
    "nvidia": {  # NVIDIA
        "github_orgs": ["NVIDIA"],                        # HEAD 200(配额耗尽复核) 2026-07-04
    },
    "arm": {  # Arm Holdings
        "github_orgs": ["ARM-software"],                  # HEAD 200(配额耗尽复核) 2026-07-04
    },
}
