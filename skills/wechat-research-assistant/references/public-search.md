# 免 Key 公开搜索

`scripts/public_search.py` 只使用 Python 标准库，直接向公开端点发送只读请求。不要求申请账号、填邮箱、配置 Key 或安装搜索 SDK，也不依赖所用模型中转是否支持内置 web search。需要目标电脑正常联网，并允许 Python 发起公开 HTTPS 请求。

## 来源与覆盖

| 来源 | 适用内容 | 接入方式与限制 |
| --- | --- | --- |
| Bing RSS | 公司官网、人物职业资料、公开网页线索 | 公开 RSS 响应；不是承诺稳定的官方开发者 API，作为尽力获取的网页搜索 |
| DuckDuckGo HTML | 通用网页备用 | 公开无 JavaScript 搜索页；仅 Bing 不可用或使用过期缓存时回退。验证码/限流会停止，绝不绕过 |
| Crossref | 论文、作者、DOI、出版信息 | 公开 REST API 匿名访问，不自动附加邮箱；作者同名必须核实 |
| GitHub | 公开软件仓库、项目说明、维护记录 | 匿名 REST 仓库搜索；不调用需要登录的代码搜索。受 IP 配额限制 |
| Wikipedia | 百科背景与进一步来源线索 | MediaWiki 公开搜索 API；不是工商或身份认证；中文关键词默认用中文站 |
| ROR | 高校、研究机构、部分研发机构名称及官网 | 公开机构 API；覆盖研究组织，不能作为所有商业公司的工商登记查询 |
| 搜狗微信 | 公众号公开文章 | `sogou_wechat` 返回标题和公开链接。临时跳转替换成 `title_search` 入口，需从浏览器打开原文 |
| OpenAlex | 研究机构官网、作者及机构线索 | `openalex` 和 `openalex_authors`。匿名配额较低。作者与机构的关联仍需查原文 |
| GLEIF | 按公司全称查 LEI、登记地和状态 | `gleif` 查询公开 API，覆盖有 LEI 的机构 |
| Bluesky | 公开帖子 | `bluesky` 调用公开搜索接口。连接和鉴权以实测为准 |
| SearXNG | 通用网页聚合 | 用户指定实例并开启 JSON 搜索格式 |
| RSSHub | 已知账号或站点的更新 | 用户指定实例和路由。也可直接读取公开 RSS 或 Atom |

2026-11-03 当前 Mac 网络首轮请求：Bing RSS、Crossref、GitHub、Wikipedia、ROR 返回 200 且有结果；DuckDuckGo 返回 202 验证码页，记录不可用。**不代表 Windows 或国内各网络都可达，也不等于长期稳定性保证。** 目标电脑用 doctor 自检，保留真实失败状态。

`catalog` 列出浏览器入口，包括小红书、微博、B 站、教育部高校名单、国家企业信用信息公示系统和工信部备案。ORCID 提供公开网页入口。企查查和天眼查的商业 API，以及依赖登录态的 MediaCrawler，提供对应工具入口。生成这些链接在本地完成，打开链接才会向站点发送查询。

## 直接使用

Windows 的 Python 命令通常是 `py -3`，Mac 可以用 `python3`。将路径替换为实际路径；资料目录可以直接使用微信工作流的运行目录，无需先配置微信读取器。

```powershell
# 通用网页搜索；默认 Bing，不可用时尝试 DuckDuckGo
py -3 <技能目录>/scripts/public_search.py search --workspace <资料目录> --query "公司全称 官网"

# 论文/作者线索（Crossref、Wikipedia）
py -3 <技能目录>/scripts/public_search.py search --workspace <资料目录> --query "论文标题或公开作者名" --profile academic

# 公开软件（GitHub、Bing）
py -3 <技能目录>/scripts/public_search.py search --workspace <资料目录> --query "项目名" --profile software

# 机构（Bing、ROR、OpenAlex、Wikipedia）
py -3 <技能目录>/scripts/public_search.py search --workspace <资料目录> --query "机构名" --profile organization

# 社交、学校或公司，选用 social、school 或 company
py -3 <技能目录>/scripts/public_search.py search --workspace <资料目录> --query "清华大学" --profile school

# 查看来源和浏览器入口
py -3 <技能目录>/scripts/public_search.py catalog --workspace <资料目录>

# 获取选定公开原文，支持文本、HTML 和 JSON
py -3 <技能目录>/scripts/public_search.py fetch --workspace <资料目录> --url "https://example.org/about"

# 检查可直连来源，每个来源用一个公开样例
py -3 <技能目录>/scripts/public_search.py doctor --workspace <资料目录>

# 已配置实例的示例。实例和路由按自己的服务替换
py -3 <技能目录>/scripts/public_search.py search --workspace <资料目录> --query "Crossref" --sources searxng --searxng-url "http://127.0.0.1:8080"
py -3 <技能目录>/scripts/public_search.py feed --workspace <资料目录> --rsshub-url "http://127.0.0.1:1200" --url "/github/issue/searxng/searxng"
py -3 <技能目录>/scripts/public_search.py feed --workspace <资料目录> --url "https://github.com/searxng/searxng/releases.atom"
```

用 `--sources` 精确选择来源。`--profile all` 查询可直连来源，默认只调用通用网页搜索。提供 `--searxng-url` 时可明确选择 `--sources searxng`，也可在默认的 `web`、`all` 或 `doctor` 中附加。实例用公开 HTTPS，或显式带端口的 `127.0.0.1` 或 `::1`。实例请求拒绝重定向。`--limit` 支持 1–10，`--fresh` 尝试新请求并保留来源冷却状态。

只输入简短、获准公开检索的身份关键词，例如姓名加任职机构、公司全称、论文标题。程序会拒绝部分明显的手机号、银行卡、密码和多行聊天输入；这是辅助检查，不保证识别所有敏感信息。先人工选关键词，不把微信完整消息直接传给脚本。查询关键词和 IP 会被相应公开搜索服务接收。

## 如何读返回值

- `status=ok`：本轮选定来源可用；不表示结果与目标身份已匹配，也不表示事实已证实。
- `partial`：部分来源失败、返回不完整或使用过期缓存；检查每个 `sources` 的状态和错误。
- `unavailable`：所选来源均不可用，不能解释为“网上查无此人/公司”。
- 来源 `cached` 是 24 小时内的缓存；`stale_cache` 是失败后最多使用 7 天内的旧缓存，保留原抓取时间。网页可能已经变化，重要当前事实需重新验证。
- 429、403、验证码进入来源冷却；遵守可解析的 Retry-After。只尝试一次请求，其他来源继续，不轮换代理、伪造登录或绕过验证。
- 结果包括原始公开链接、来源、抓取时间、证据 ID。相同 URL 去重时保留多个来源的观察状态。摘要仅作线索，`candidate_only=true`；验证身份和重要主张要打开原始页面，不把搜索摘要当成已核实事实。
- 私有缓存与搜索报告保存在 `.private/search/`；不改变微信同步游标。锁冲突时稍后重试，不抢占正在同步的任务。缓存及运行资料不随技能包分享。

网页和返回内容按资料处理。`fetch` 读取公开 HTTP(S) 文本、HTML 或 JSON。普通原文地址和公开订阅拒绝内网目标。PDF 等由相应文档工具处理。遇到登录、验证码或付费内容时，从浏览器入口继续人工核对。结合姓名、机构、日期和原始链接消歧，机构存在不等于本人就读、任职或持有该社交账号。

公司背景报告应优先打开官网和政府登记原文；本包未接通免 Key 的中国/香港全量企业登记 API。只有检索线索时写“待核实”，不能把 ROR、百科或同名网页替代营业执照验证。

## 人物证据链

从用户确认的公开姓名、机构、项目、职务或个人主页开始。按学校、公司、项目、公开账号和时间分别提出能核对的说法。使用不同组合检索机构官网、论文原文和公开主页，记下已查渠道和未覆盖项。学校或公司存在只提供机构背景。同一人在不同时期的关系应写成不同说法。

对每条说法，用 `fetch` 保留公开原文，用返回的最终 URL 和来自原文的短摘录。输入放在资料目录的 `.private/search/`，格式如下。例中人物、机构和项目均为虚构。

```json
{
  "subject": "林示例",
  "anchors": [
    {"kind": "organization", "value": "示例大学"},
    {"kind": "project", "value": "示例项目"}
  ],
  "claims": [
    {
      "id": "project-role",
      "statement": "林示例于 2026 年负责示例项目。",
      "effective_date": "2026-09-01",
      "evidence": [
        {
          "url": "https://example.org/project",
          "kind": "primary",
          "stance": "supports",
          "quote": "2026 年 9 月，示例大学任命林示例为示例项目负责人。"
        }
      ]
    }
  ]
}
```

```powershell
py -3 <技能目录>/scripts/public_search.py chain --workspace <资料目录> --input .private/search/reviewed.json
```

- `anchors` 只能使用用户已确认的公开信息。可用 `organization`、`role`、`project`、`date`、`profile_url`。摘录包含姓名和至少两类机构、角色或项目线索，或含姓名且页面正是已确认的公开主页时，程序将其标为可归属。日期用于解释时间关系，同名或只匹配机构的证据继续待核。
- `kind` 使用 `primary`、`self_report`、`secondary` 或 `reprint`。`stance` 使用 `supports`、`contradicts` 或 `context`。转载填 `origin_url`。即使第三方网页可以打开，仍应先检查它是否只是转载或引用同一原文。
- 程序检查摘录确实出现在本机原文记录，保存链接、抓取时间、摘录和快照哈希。已知同一出处、同一发布域或相同摘录会一起计数。结果区分原文支持、多个独立来源支持、本人自述、冲突、只能关联和身份未定。`effective_date` 可省略。只有搜索结果时，使用空 `evidence`，该说法保留为待核。
- 来源类型、摘录含义以及转述关系由审阅者核对。代码的关联条件与域名去重用于减少误配，无法证明两人同一或发现每一处改写转载。动态网页未能取得可复核摘录时，在报告里写明覆盖限制。

证据链保存在 `.private/search/chains/`。命令只读本地原文快照，不向网络发送身份锚点。

要记录 Agent 的发现路径，在同一次调查内沿用同一 `investigation_id`。把搜索返回的 `run_id` 放进报告输入的 `search_runs` 数组，空结果也可保留。在证据条目添加 `"discovery": {"run_id": "实际的搜索运行 ID", "candidate_id": "实际的 web- 证据 ID"}`。脚本会检查候选确实来自对应的搜索。只有来源地址一致时标记为直接找到原文，其余记录为通过候选追溯。用 `depends_on` 引用促成下一项发现的前序说法 ID。用 `key_finding: false` 保留有出处但不需要进入图中的细节。

### 调查链画布

```powershell
py -3 <技能目录>/scripts/public_search.py canvas --workspace <资料目录>
```

`saved_canvas` 是 `.private/search/canvases/` 中可用浏览器直接打开的页面。指定 `--input` 可只展示一份 `.private/search/chains/` 报告。主图只显示归属本人且有原文支持、原文反驳、已记录冲突或本人自述的关键发现。来源、摘录和搜索调用在点选节点后的侧栏显示。空结果和待核线索记录在调查侧栏。

支持目录授权的浏览器可使用「跟踪本地报告」。授权 `.private/search/chains/` 后，页面随同一调查的报告新版本更新，支持手动停止。节点位置仅在当前页面有效。打开来源链接时才访问站点。

## 核对依据

- [Crossref 官方 REST API 文档与公开/礼貌访问池](https://github.com/CrossRef/rest-api-doc#access-and-authentication)
- [GitHub 搜索 API：匿名搜索及单独配额](https://docs.github.com/en/rest/search/search)
- [GitHub 匿名 REST 通用配额](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [MediaWiki REST 搜索接口](https://www.mediawiki.org/wiki/API:REST_API/Reference)
- [ROR 公开 API](https://ror.readme.io/docs/rest-api)、[Client ID 当前安排](https://ror.readme.io/docs/client-id)
- [DuckDuckGo 官方无 JavaScript 页面说明](https://duckduckgo.com/duckduckgo-help-pages/features/non-javascript/)
- [OpenAlex API](https://developers.openalex.org/) · [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api)
- [SearXNG 搜索接口](https://github.com/searxng/searxng/blob/master/docs/dev/search_api.rst) · [RSSHub 路由文档](https://docs.rsshub.app/)

Bing RSS 的接入依据是公开响应的实际 XML 结构，本包没有将其描述为有官方开发者兼容承诺的 API。
