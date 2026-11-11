# TVBox 聚合配置（自动更新 · 可观测 · 可回滚）

自动聚合多个公开 TVBox 配置源，去重、修复相对路径、内容过滤、每日存活复检、**成品卫生清理与可用性排序**、首页推荐探测、**端到端可播率探测**、多日稳定性判定与回归断言后，输出 10 份可直接使用的配置（7 份基础/排序版 + 3 份场景版）。每天北京时间 04:00 由 GitHub Actions 重建并提交，同时产出一份[复查报告](https://raw.githubusercontent.com/dimogu/tvbox-merged/main/reports/audit-latest.md)。

> 功能之外，这套流水线还回答三个问题：**源到底能不能播**、**今天的判定可信吗**、**生成了坏版本怎么退回**。

---

## 一、配置地址（国内推荐直接复制整行链接）

按用途挑**一个**填进「设置 → 配置地址」。不知道选哪个：先用 `tvbox-quality.json`（精播版）；老盒子/国内直连用 `tvbox-cn-fast.json`；想长期不动用 `tvbox-stable7.json`；想一个节点都不少用带 `homefirst` 的版本。

> ⚠️ **链接请一律复制下面「主线路（GitHub Pages）」里的 `https://dimogu.github.io/tvbox-merged/...`**。jsDelivr 的 `@main` 及各镜像有顽固的边缘缓存，实测曾同时挂着 4 个不同年代的旧版；旧版含客户端不兼容的 `style` 对象，导入会直接报 `IllegalStateException`。

| 文件 | 站点数（本轮快照，每日浮动） | 用途 |
|---|---|---|
| `tvbox-quality.json` | ≈ 655 | **精播版**：只留实测可播 / 首页实测有影片 / jar 实测有效的节点，宁缺毋滥 |
| `tvbox-cn-fast.json` | ≤ 120（本轮 92） | **国内极速版**：国内直连、无 spider jar、无爬虫源，最多取前 120 个，开屏最快 |
| `tvbox-stable7.json` | 视历史累积（本轮 89） | **7 天稳定版**：近 7 天多次可用且当前无连败，不追新源、不随上游抖动 |
| `tvbox-full-homefirst.json` | ≈ 2960，前 ≈ 990 个实测可用 | **全量 + 首页优先**，功能最全，依赖 spider jar |
| `tvbox-lite-homefirst.json` | ≈ 1210 | **精简 + 首页优先**，真·不加载 jar，低性能盒子首选 |
| `tvbox-network-homefirst.json` | ≈ 520 | **网络优化 + 首页优先**，无 jar、无境外托管，国内直连最稳 |
| `tvbox-home.json` | ≈ 990 | 只保留首页可用节点（含同 jar 推断，推断项排在实测之后） |
| `tvbox.json` | ≈ 2960 | 全量版，按「可用 → 未知 → 降级」排序 |
| `tvbox-lite.json` | ≈ 1210 | 精简版，按「可用 → 未知 → 降级」排序 |
| `tvbox-network.json` | ≈ 520 | 网络优化版，按「可用 → 未知 → 降级」排序 |

站点数随上游每天都在变，上表只是快照。**精确值看配置里的 `_meta.sites`、首页优先版的 `_meta.home_first_count`，或打开[健康页](#四健康页与失败告警)**（三个场景版是筛选版，没有 `home_first_count` 字段）。健康页还带**线路新鲜度自检**：浏览器实时取回 Pages / jsDelivr `@main` / 无版本号路径，比对本轮站点数，哪条线路还挂着旧版一眼可辨。

> **「无境外托管」过滤的到底是什么？** 不是内容审查，也不看播什么片子。它只读节点的 `api` / `jar` / `ext` 三条字段里的**域名**，判断这份配置文件本身住在哪台服务器上：如果挂在 `raw.githubusercontent.com` 或 GitHub 反代上，国内盒子经常拉不到、首页一直转圈，所以网络版不收这类节点（它们在全量版和精简版里都还在）。`netflix.com` 这类影视站不在名单里；国内 `gitee.com` 与零命中的 `vercel` / `netlify` / `pages.dev` 也不收。

### 三个场景版：怎么筛出来的

`scripts/variants.py` 把同一天的 5 份探测报告按站点签名对齐，产出三份「按用途直接选」的成品：

| 版本 | 保留条件 | 排序 | 额外处理 |
|---|---|---|---|
| 精播版 `tvbox-quality.json` | 端到端实测可播 **或** 首页影片列表实测非空（ok） **或** jar/ext 实测有效（likely，不含同 jar 推断） **或** 误杀复核复活 | 可播 > 首页 ok > 首页 likely > 可播得分 > 近 7 天可用天数 | 剔除明确探活失败的解析通道 |
| 国内极速版 `tvbox-cn-fast.json` | 在网络优化版基础上：仅 http 采集源、无 `type=3` 爬虫源、api/jar/ext 均不落在境外托管，且满足上述质量条件 | 可播 > 首页 ok > 可播得分 | 取前 120 个；parses 取存活优先的前 40 个；去掉指向境外的全局 `spider` |
| 7 天稳定版 `tvbox-stable7.json` | 历史 ≥3 天：`home_days_7 >= min(3, 天数-1)` 且 `fail_streak == 0` 且**非同 jar 推断**；历史不足 3 天：只认今日首页影片实测非空（`ok`，无连败）或端到端实测可播 | 近 7 天可用天数 > ok 天数 > 可播得分 > 首页等级 | 连续 2 天以上探活失败的节点排除；推断节点与「仅 jar 有效」一律不收 |

降级保护：首页探测报告缺失时按 `tvbox-home.json` 顺序视为 likely；筛选结果低于 60 个时保底给出首页版节点；任何情况下都不会产出空配置，是否降级写在 `_meta.filter.history_fallback` / `home_fallback` / `_meta.evidence` 里。

> 口径边界：端到端可播率每天只抽样 30 站，**未抽样站点是「首页实测通过」而非「实测能播」**。精播版是「去掉已知坏节点」，不是「保证每个节点都能播」。稳定版在历史累积到 3 天之前等同于今日实测，之后会自动收紧 —— 这一点 `_meta.filter.history_days` 会如实写明。两种口径都排除同 jar 推断项：否则推断节点在历史里同样会被记成「连续可用」，历史满 3 天那天版本会从百来个实测节点暴涨到上千个没实测过的节点。

### 三份基础配置的复查（每天自动出一份报告）

`scripts/polish.py` 在聚合之后、首页探测之前跑一遍，`scripts/audit.py` 再把结果独立复核成 [`reports/audit-latest.md`](https://raw.githubusercontent.com/dimogu/tvbox-merged/main/reports/audit-latest.md)。查的是「会让用户直接踩坑、但不至于让构建崩」的那类瑕疵：

| 查什么 | 复查前（2026-11-10 实测） | 现在 |
|---|---|---|
| 空 api 的占位站点 | 8 个 `--- 1 ---` 分隔符被当成站点 | 0 |
| 非法 api | 7 个（不是 http:// 或 csp_ 开头） | 0 |
| 同名节点 | 21 组 55 个，列表里三条一模一样的「AList」 | 0（追加域名后缀） |
| **重复 / 空主键 `key`** | **174 组 284 个**（`push_agent`×19、`360`×3…），客户端里后一个站点顶掉前一个 | 0（保留首个、其余加稳定后缀） |
| **`ext` 是 JSON 对象** | **382 个站点 + 104 个解析通道**：TVBox 原版用 Gson 严格解析，对象型 `ext` 会让**整份配置**报格式错误 | 0（统一转成紧凑 JSON 字符串） |
| **解析通道同名** | **15 组**（`飞云`×2、`8090`×3…），用户根本分不出该选哪个 | 0（追加域名后缀） |
| **元信息条数不符** | 精简版写「108 条直播 / 192 个解析」，实际 101 / 80；场景版更离谱（写 187 实际 170） | 0（每份按自身内容重算） |
| **壁纸地址不可达** | `wallpaper-1tg.pages.dev` 国内实测超时，开屏先卡几秒再退回默认 | 已移除（客户端用内置背景） |
| **直播源混排** | 98 条里 37 条走 `raw.githubusercontent` / GitHub 反代，国内必然是空频道，却排在最前面 | 境外托管稳定沉底（不删） |
| 参数级重复接口 | 43 个同一接口的不同写法 | 0 |
| ext 是整数的畸形站点 | 1 个 | 0 |
| 解析 url 非法 | `Demo` 这类占位 5 个 | 0 |
| 解析 `type` 写成字符串 | 1 个 | 0 |
| 直播 `type` 非整数 / 重名 | 15 / 42 | 0（归一 + 加后缀） |
| 精简版是否真不依赖 jar | 带全局 spider + 6 个 jar 站点 | 0 |
| 网络版是否真无境外依赖 | spider 指向 `raw.githubusercontent.com`，另有 `repl.co` 漏网 | 0（名单已收敛，见下） |
| 解析截断口径 | 按文件顺序砍前 N 条，先砍掉的是最好用的 json 聚合通道 | 60% json 聚合 + 40% webview 嗅探按配额截断 |
| 排序 | 按名称拼音排，昨日 860 个实测失败节点在最前面 | 本轮可用 579 / 未知 1174 / 降级 1210（每日随探活变化） |

三条原则：

- **只沉底不删除**：降级层（昨日首页实测失败、csp 爬虫缺 ext）仍留在配置里 —— 误杀率有 36%~52%，所以只排到后面；次日探活恢复会自动回到前排。昨日被真实 TVBox UA 复核复活的节点直接进「未知」层，不背这口锅。直播源同理：境外托管的那 37 条只是排到末尾，没有删。
- **改名不动数据**：同名节点追加 ` · 域名`（csp 源用爬虫类名或 ext 域名）。代价是该节点当天的历史签名断链、回落到「未知」层，次日自动接回。
- **身份签名 = 名称 + api + jar + ext**（不含 `key`，且 `ext` 先归一再哈希）：所以主键去重、`ext` 转字符串这类批量修复，不会让整批站点的历史一起断掉 —— 这是上一轮改 `key` 时踩到的坑。
- **「不依赖 jar」现在可验证**：`type=3` 里 api 是 `.js` / `.py` 的属于 JS / Python 爬虫，不需要 jar，予以保留；真正要 jar 的（`csp_*` 与带 `jar` 字段的站点）已从精简版 / 网络版剔除。

这些口径已经变成断言（`structure_clean` / `site_key_unique` / `ext_is_string` / `meta_counts_consistent` / `parse_names_unique` / `no_foreign_fields` / `wallpaper_reachable` / `lite_network_jar_free` / `network_hosting_clean`）：下次构建再出现空条目、重名、重复主键、对象型 ext 或配置落回代码托管，会直接阻断提交，线上保持上一版。


### 怎么填链接（推荐：GitHub Pages）

GitHub Pages 是「总是最新」入口：缓存只有几分钟、每次构建自动部署、不受 jsDelivr 12h 边缘缓存影响，国内一般也能直连。按上面「一、配置地址」表格选一个文件名，统一前缀 `https://dimogu.github.io/tvbox-merged/<文件名>` 即可（`tvbox-quality.json` 等 10 个文件名见上表）。

> 首次使用需在仓库 Settings → Pages 把 Build and deployment 的 Source 设为 **GitHub Actions**（一次性操作），之后每次构建自动部署。

### 备用线路（可选，可能滞后）

> **只用 Pages 链接就够。** 备用线路（jsDelivr `cdn/fastly/gcore.jsdelivr.net/gh/dimogu/tvbox-merged@main/<文件名>`）可达，但有最长 12h 边缘缓存、purge 不保证生效，更新后几小时内可能仍是上一版；导入报 `IllegalStateException` 也先换 Pages。
>
> 固定稳定版 tag、raw 官方源、commit-sha 不可变链接等完整备用方案，已整理到仓库 **`README.full.md`**，需要时查阅。

### 导入报「配置取得失败 / java.lang.IllegalStateException」怎么办

按顺序试，基本都能解决：

1. **改用 Pages 链接**：`https://dimogu.github.io/tvbox-merged/<文件名>`。Pages 缓存只有几分钟且每次构建自动部署，不受 jsDelivr 12h 边缘缓存影响 —— 曾出现 CDN 上还挂着 484 站点的旧版、仓库已是 1212 站点的情形。
2. **清客户端缓存**：设置 → 缓存 → 清除，再重新导入；旧配置可能被客户端本地缓存住。
3. **先用小体积版本验证**：`tvbox-cn-fast.json`（约 53 KB）或 `tvbox-network-homefirst.json`（约 136 KB）。全量 homefirst 约 850 KB，在慢网络（<50 KB/s）下仍可能超过客户端的拉取超时。
4. 仍失败就把 toast 里**完整的异常文本**（长按可复制）发出来，尤其是 `Expected a string but was BEGIN_OBJECT` 这类后半段。

构建侧已做两道防护：上游常把 `style` / `header` 写成 JSON 对象，而 TVBox 原版、影视仓把这两个字段声明成 `String`，Gson 会抛 `IllegalStateException` 让整份配置导不进来 —— 现在统一降级（只影响卡片布局 / 请求头，不影响可播性），并由断言 `client_compat_clean` 硬阻断复燃；同时配置改为紧凑 JSON，同样内容体积少约四分之一。

### 播放时闪退 / 重启（NullPointerException: String.trim()）怎么办

这**和上面的导入失败是两个不同的问题**：导入失败是 `IllegalStateException`（Gson 字段类型不匹配），播放崩溃是 `java.lang.NullPointerException: Attempt to invoke virtual method 'java.lang.String.trim()' on a null object reference`，发生在 `VideoActivity` 的播放路径 —— 此时配置其实已经能正常导入了。

根因在客户端一侧：FongMi / 影视仓 5.6.4 的 `Site.setExt()`、`Live.setExt()` 写成 `this.ext = ext.trim()`，`UrlUtil.convert()` → `UriUtil.uri()` 第一行也是 `url.trim()`，都没有判空；而 Gson 遇到配置里**缺失的 String 字段**不会填默认值，字段就是 `null`，点播 / 切线路重新初始化站点时直接抛异常让应用退出重启。

按顺序试：

1. **升级客户端到 5.6.8+**：新版已给 `Value.getV()`、`Vod` 各 getter 加了 `TextUtils.isEmpty` 判空，这是根治办法。
2. **换精简线路**：`https://dimogu.github.io/tvbox-merged/tvbox-cn-fast.json`（92 个标准采集站、零 csp / 零 jar、仅 40 条解析），不走 spider，最不容易碰到上游返回的脏播放数据。
3. **清客户端缓存**：设置 → 缓存 → 清除，必要时再清系统里的应用缓存；旧配置 / 旧播放记录可能缓存着坏数据。
4. 只在**某一条线路或某一个站点**崩时，直接在客户端里换线路或隐藏该站点 —— 通常是该站点返回了空播放地址。

构建侧已加防线：客户端会裸 `trim()` 的 String 字段（`ext` / `api` / `jar` / `name` / `url` 等）缺失或为 `null` 时统一补空串，断言 `no_null_strings` 硬阻断复燃；`parses.ext` 因各分支类型不一致（FongMi 新版是 `Ext` 对象、老版是字符串）保持原样。**注意**：配置只能消除自身提供的 `null`，上游站点返回的播放数据由客户端负责判空；若升级客户端后仍崩，请把崩溃框里完整的异常文本发出来。

### 固定稳定版（内容永不变化，可回滚）

每日构建覆盖 `main` 上的文件；若想锁定不变，用 `stable.json` 里的 tag 链接（[在线查看](https://raw.githubusercontent.com/dimogu/tvbox-merged/main/stable.json)，健康页也会列出）。`stable.json` 只列该 tag 里**确实存在**的文件，不会给出 404 链接；tag 需回归断言连续通过 3 天才推进。

要退回旧版：

```bash
python scripts/stable.py --rollback stable-YYYYMMDD
```

> 当前 tag 推进前的过渡期说明、完整备用链接与缓存原理，见仓库 `README.full.md`。

---

## 二、首页推荐：怎么判定、怎么排序

TVBox 的**主界面推荐**和**搜索**是两套数据，能搜到片子不代表能出首页：

| 站点类型 | 首页数据从哪来 | 常见空白原因 |
|---|---|---|
| CMS 采集源 (`type=1`) | `?ac=videolist` / `?ac=detail` / `?ac=list` 的影片列表 + 分类 | 只返回分类不返回影片、接口超时、站点只做搜索源 |
| `csp_*` 爬虫源 (`type=3`) | jar 里的 `homeContent()`，分类规则常在 `ext` 指向的 JSON | jar 404 / 不是 jar / ext 首页配置失效 |

`scripts/home_probe.py` 每天在云端逐站探测，判定等级：

| 等级 | 判定依据 | 纳入首页版 |
|---|---|---|
| `ok` | CMS 首页影片列表实测非空 | ✅ 排最前 |
| `likely` | jar 有效且 ext 首页配置有效 | ✅ |
| `likely`（同 jar 推断） | 同一 jar 下已有站点可用，其余「无 ext、依赖内置首页」的 `unknown` 召回（实测首轮召回 529 个） | ✅ 同级内排实测之后 |
| `classes_only` | 只返回分类、没返回影片 | ✅ 排最后 |
| `unknown` | 依赖 jar 内置首页，HTTP 无法判定 | ❌ |
| `fail` | jar 404/非 jar、ext 失效、接口连不上 | ❌ |

**首页优先版不删任何节点**，只重排。排序权重（可靠度从高到低）：

```
今日首页等级  >  近 7 天可用天数  >  端到端可播得分  >  探测顺序
```

「近 7 天可用天数」来自 `quality.json` 的判定历史，用来抑制上游抖一下就换首位的跳动；「可播得分」来自当天的端到端探测。其余节点严格保持原顺序，搜索与切换源的能力完全保留。

可读清单（每行 `等级 | 站点名 | 判定依据`）：`https://raw.githubusercontent.com/dimogu/tvbox-merged/main/home-sites.txt`

> 坑：不少作者把 jar 伪装成 `.jpg` / `.png` 传到图床（实测返回 `PK\x03\x04` ZIP 魔数）。判断 jar 是否有效**只能看内容**，按后缀判会误杀几百个节点。

---

## 三、判定可信度：从「配置活着」到「片子能播」

`scripts/play_probe.py` 每天抽样 30 个 CMS 站点跑完整链路：

```
首页列表 ?ac=videolist&pg=1 → 取 vod_id → 详情 ?ac=detail&ids= → 抽 vod_play_url
→ HEAD/Range GET 校验播放地址；另外探一次搜索 ?wd= 和抽样验证 parses
```

结果写进 `play_summary.json` 与各配置 `_meta.play_rate`，并作为排序权重。健康页展示**列表可用率 / 详情有地址率 / 播放可连率 / 搜索可用率 / parses 存活率**的趋势。

`scripts/recheck.py` 从被判 `fail` 的站点里按日期固定抽样 50 个，换 3 种真实 TVBox 客户端 UA 重测，算出**误杀率下界**。这个数字决定「fail 到底有多可信」；复活的站点会从「连续失败计数」里清零，避免被误杀淘汰。

`scripts/history.py` 每天写一份 `history/YYYY-MM-DD.json`（保留 14 天），汇总出 `quality.json`：

- 站点近 7 天可用天数、连续失败天数 → 排序与多日淘汰依据；
- 采集接口探活**连续失败 3 次才淘汰**（`DEAD_AFTER_FAILS` 可调），上游抖一天不再改动线上配置；
- 上游连续失败天数（≥3 天健康页标红）；
- 断言连续通过天数（稳定版指针的推进依据）；
- 近 14 天站点数 / 首页可用 / 可播率 / 误杀率趋势。

**jar / ext 可达性盲区已补上**：旧流程每天只探采集站 api 接口（`?ac=list`），从不检查 spider jar 与远程 ext 是否还活着 —— 实测 jar 抽样可用率常年在 25% 上下，失效 jar 却一直留在配置里、用户点开就空。现在新增 `scripts/jar_health.py`，逐日探测所有唯一 jar / ext URL，死 jar 进入 `quality.json` 的 `jar_fail_streak`（连续失败天数），**连败 ≥2 天的死 jar 所依赖的 csp 站点会被自动沉底**；并修复了一类上游把 `.jpg/.png` 图片塞进 `jar` 槽的脏数据（352 个站点，客户端照拉必崩，已清洗并在断言里零容忍）。健康页新增「jar 健康」卡片与说明。

所有探测都在 GitHub Actions 云端网络执行，每份报告的 `env` 字段都写明判定环境、run id 与是否走代理 —— 本机网络跑出来的结果不会被误当成线上结论。

---

## 四、健康页与失败告警

**https://dimogu.github.io/tvbox-merged/** （Pages，随每日构建更新）

一页展示：各版本站点数与完整直链、上游源健康表（连续失败 ≥3 天标红并建议更换）、首页判定分布、近 14 天趋势、端到端可播率、误杀率、回归断言状态、稳定版固定链接。结构化数据在 `health.json` / `site/health.json`。

---

## 五、自动更新的保险丝

每天无人值守覆盖线上同一个文件，没有回归检查的话，某天上游改格式就会把坏配置推给所有客户端。因此：

**1. 提交前回归断言**（`scripts/assertions.py`，任一硬检查失败即中止，线上保持上一版）

- 所有配置是合法 JSON、**无重复 key**
- `_meta.sites` 与实际站点数一致；站点数不低于上一版的 60%（防骤降 / 空配置）
- **成品卫生**：无空 / 非法 api、无重名节点、解析 url 合法、直播与解析的 `type` 都是整数
- 每个 `csp_*` 站点必须有 http `jar`
- **精简版 / 网络版 0 个 jar 依赖**（无全局 `spider`、无 `jar` 字段、无 `csp_*`）
- **网络优化版不得残留「配置住在境外代码托管 / 反代上」的节点**。名单只收两类、且都有实测命中：代码托管平台（`githubusercontent.com` / `github.com` / `github.io` / `gitlab.com` / `gitlab.io` / `bitbucket.org` / `sourceforge.net`）与 GitHub 反代（`ghproxy` / `gh-proxy` / `gitmirror` / `fastgit` / `kgithub` / `moeyy`、`repl.co`）。**检查的是 `api`/`jar`/`ext` 的域名，与片源内容无关** —— 播什么片子不受影响，`netflix` 这类影视站不在名单里；`gitee.com`（国内）与 `vercel`/`netlify`/`pages.dev` 等零命中平台也不再收
- 无残留 `./` `../`、无成批 `XBPQ/XBPQ/` 类重复路径段
- 首页版 ⊆ 全量版；三个 homefirst 版本站点数与对应源完全一致；已生成的场景版也必须是全量版子集
- 过滤后站点 / 直播 / 解析均无违规关键词残留
- 抽样 20 个 jar（必须 200 + `PK` 魔数）与 20 个 ext，采用「绝对下限 + 相对昨日骤降」判定（第三方链接本就大量失效，固定高阈值会天天误挡）
- 三份场景版为**可选产物**：首日构建或探测失败来不及产出时只记警告，不阻断 7 份基础配置；一旦存在就同样校验 JSON、元信息与子集关系

**2. 归档与回滚**：`archive/YYYY-MM-DD/` 存最近 7 天的轻量备份（`manifest.json` + lite/home 的 gzip），完整版本由 git 历史与不可变 tag 回滚。

```
# 查看当前稳定版指针
cat stable.json

# 回滚到上一个 tag
python scripts/stable.py --rollback stable-20261109
```

**3. 生成产物只由 Actions 提交**：所有 `tvbox*.json`、`health.json`、`quality.json`、`stable.json`、`home-sites.txt`、`history/`、`archive/` 都在 `.gitignore` 里（新文件不会被本地误提交），workflow 用 `git add -f` 独家提交。**已跟踪的旧配置不做 `git rm --cached`**：它们本身就是回退基线，一旦从索引移除，某天断言失败 Actions 不提交时线上直链会直接 404，连上一版都没得退。本地执行 `python scripts/local_readonly.py` 给这些文件加 `skip-worktree`，`git status` 便永远干净（`--status` 查看、`--undo` 解除）。**但 Actions 每天会提交生成物，所以本地推送源码前仍必须先 `git pull --rebase origin main`**，否则会遇到非快进冲突；本机直连 GitHub 常超时，用 `git -c http.proxy=http://127.0.0.1:7890 pull --rebase origin main` 更稳。

**4. 本地试跑保护**：非 Actions 环境下，所有脚本自动把输出改成 `_local-*.json`，绝不覆盖线上配置（曾因本地试跑把 lite 版覆盖成单源版）。确实要写正式文件时才设 `ALLOW_LOCAL_WRITE=1`。

---

## 六、上游可持续与体积治理

- `sources.txt` 存 29 个源（每行 `名称|URL[|相对路径基准]`，`#` 为注释），改文件提交即可增删，**不用改代码**。单个源失效只跳过它。
- **候选发现（约每 3 天）**（`.github/workflows/discover.yml`）：约每 3 天自动跑 `scripts/discover.py`，挖导航仓库 README / 仓库 tree / 多仓聚合，相对**线上真实配置**算「去重后边际贡献」，产出 `reports/candidates-YYYY-MM-DD.md` 与 `candidates.json`。人工看一眼后一键并入：

  ```bash
  python scripts/merge_candidates.py --take 5 --dry-run   # 先看会加什么
  python scripts/merge_candidates.py --take 5             # 追加到 sources.txt
  ```

- **关键词外置**：`blocklist.txt`（每行一个正则，改文件即生效），同时扫站点名 / api / ext / jar、直播、解析，不再只扫站点名。
- **按内容指向去重（名称不参与）**：`common.content_url()` / `content_key()` 把 `raw.githubusercontent.com`、`github.com/.../blob`、`cdn|fastly|gcore.jsdelivr.net/gh`、`gh-proxy` 反代，以及 `www.`、http↔https、默认端口、尾斜杠、参数顺序、缓存参数，全部还原成同一把钥匙；站点按 `api + ext + jar` 判重，直播 / 解析按 url 判重。于是「❤ 野荷塘 / 😘野荷塘」这类换皮名、「南风直播源 / Yoursmile」这类同一份 `live.txt` 挂在 raw 与两个反代域名上的重复，只留一条；反过来**同名但 ext 不同的站点不会被误杀**。数量记在 `_meta.polish.removed.dup_content` 与健康页「内容去重」列，`assertions.py` 的 `no_content_duplicates` 会硬阻断复燃。
- **jar 分级 + 网络优化版**：`_meta.jar_count` / `jar_top` 统计依赖，`tvbox-network.json` 只保留不依赖境外代码托管 jar/ext 的 http 站点，给低端盒子与国内直连场景。
- **自托管入口**（合规 / 内网备用）：在 NAS、软路由或家里一台常开机器上跑，局域网设备填内网地址，避免公开仓库被投诉下架后所有设备同时失效：

  ```bash
  python scripts/serve.py --port 8080 --refresh 21600
  # 设备填：http://192.168.1.10:8080/tvbox-lite-homefirst.json
  ```

  上游拉不到时继续用上次成功缓存，绝不返回空配置；提供 `/` 状态页、`/health`、ETag 与 CORS。

- **已知限制：全局 `spider` jar 仍在 `raw.githubusercontent.com`。** 它是本轮约 1744 个 `type=3` 爬虫节点的运行底座（2.9 MB），国内直连拉不到就是整片空白。这个文件不能搬：jsDelivr 对 `.jar` 直接 403，自建镜像又属于再分发别人的产物，所以只如实标注、不做镜像。**国内直连请用 `tvbox-cn-fast.json` / `tvbox-network*.json`** —— 这两份 0 个 jar 依赖，`csp_*` 与带 `jar` 字段的站点在体检阶段就被剔掉了；全量 / 精播 / 7 天稳定版依赖代理或能直连 GitHub 的网络。
- **已知限制：`lives` 的 37 条境外直播源只是沉底，没有删除。** 有代理的用户仍能用；只是不再挡在列表最前面。


---

## 七、自动化机制

- **每天北京时间 04:00** 由 `.github/workflows/update.yml` 构建；**约每 3 天**由 `discover.yml` 找新源；两者都支持手动 `Run workflow`。
- 主流程：`build.py` → **`polish.py`（卫生 / 去重 / 消歧 / 排序）** → **`audit.py`（复查报告）** → `home_probe.py` → `recheck.py` → `play_probe.py` → `rank_home.py` → `variants.py`（三个场景版）→ **`assertions.py`（失败即停）** → `jar_health.py` → **`regression.py`（全历史问题复核）** → `history.py` → 提交配置与历史 → `stable.py`（归档 + 不可变 tag）→ `health.py` → 健康页结构校验 → 上传 Pages artifact → 提交归档与指针 → push → 最多 3 轮 purge CDN（含 sha256 校验）→ 部署 Pages。
- 内容无实质变化时不写文件；判定历史每天仍会留痕，便于看趋势。
- 首页探测、误杀复核、可播率探测为 `continue-on-error`（单点网络抖动不影响主配置产出），只有**回归断言是硬阻断**。

## 八、全历史问题回归复核

`assertions.py` 只回答「今天这份能不能提交」，管不到「三个月前修好的东西有没有被后续改动悄悄弄坏」。`scripts/regression.py` 把九轮迭代里真实出现过的故障逐条做成可复跑的检查，分三组：

| 组 | 复核什么 | 例子 |
|---|---|---|
| 产物契约 | 10 份配置本身的字段、去重、元信息 | 对象型 `style` 导入报错、裸 `trim()` 字段 NPE 闪退、图片被当 jar、`jar_count` 照抄全量版、精简版偷偷带 spider |
| 构建链路 | 工作流顺序、静默失败、缓存与文档口径 | polish 必须早于 home_probe、`git add -f` 不能整条吞失败、purge 双路径、stable tag 3 天门槛、README 与健康页线路口径 |
| 线上线路（`--online`） | Pages / jsDelivr `@main` / 无版本号路径 | 逐条取回 sha256 与仓库 HEAD 比对，CDN 挂旧版当场暴露 |

产出 [regression-latest.md](https://dimogu.github.io/tvbox-merged/regression-latest.md)（每次构建同步到 Pages）与 `_regression.json`，健康页有汇总卡片与未通过项列表。默认完全离线，改完代码随时可跑：

```bash
python scripts/regression.py                          # 离线复核
python scripts/regression.py --online --proxy http://127.0.0.1:7890   # 本机加线上比对
```

> 与「健康页线路新鲜度自检」配合：自检是**用户打开页面那一刻**由浏览器实时取回比对，能发现 CI 校验之后才出现的边缘节点滞后；`--online` 是构建/排查时的服务端视角。历史上 CDN 曾同时挂着 4 个年代不同的旧配置，用户填旧链接就会报 `IllegalStateException`，这两道就是针对它的。

### 本轮复核发现并修复的问题

1. `update.yml` 主提交与归档提交把多个 pathspec 放在一条 `git add -f` 并用 `|| true` 吞错 —— 任一文件不存在就会静默不提交当天全部产物。已改成逐文件判断 + 必需产物显式报错。
2. 健康页的 `</table>` 缩进写在 for 循环体内，线上 5 个开标签 / 33 个闭合，28 个上游源的行被浏览器丢弃。已移出循环，并在部署前加结构校验硬步骤。
3. 健康页「国内直链 / fastly 镜像」给的是不带 `@main` 的路径，恰好是 README 明令禁用的最易滞留旧版的形式。已统一补 `@main`，并新增浏览器实时线路自检。
4. 三个场景版（含首页版）整份继承全量版 `_meta`，`jar_count` 写 53 而实际只有 19 / 0，还有 `home_first_note`「未删减」等不实描述。已按各自站点重算并剔除 build 期字段，断言新增 `meta_jar_consistent` / `collections_not_empty` / `all_versions_subset_of_full`。
5. `;md5;` 字面剥离与仓库其它地方的通用切分口径不一致，`url;<md5>` 写法会带后缀去请求被误判死链；`jar_health` 只判 HTTP 200，图床返回的 HTML 会被当成「jar 还活着」，死 jar 永远不沉底。已统一按客户端 `split(";")[0]` 并恢复 ZIP 魔数判定。
6. 健康页把「当前连败条目数」说成「≥2 天连败、已沉底」；`stable.json` 在 tag 复用时把指针 commit 写成当前 HEAD。两处口径已修正，另修了 `purge_cdn` 的重复请求 / `None` 漏报与 `polish` 的 lives/parses 半量兜底。

## 合并时处理的坑

1. **相对路径**：上游大量使用 `./js/x.js`、`./jar/x.jar`，合并后必须改写为绝对 URL；
2. **spider jar 冲突**：一份配置只能有一个全局 spider，不同源依赖不同 jar，因此把各源原配 jar 写入站点级 `jar`；
3. **重复**：统一按「内容指向」去重 —— 站点看 `api + ext + jar`（`csp_*` 的内容由 `ext` 决定，名称只是显示用的皮），直播 / 解析看归一化 url；raw / jsdelivr / gh-proxy / github blob 等链接别名先还原成同一份。名称完全不参与判重，既不会漏掉换皮名，也不会误杀同名不同配置的站点；
4. **失效源**：连续 3 天探活失败才剔除，次日恢复即重新纳入；
5. **内容过滤**：外置关键词，站点 / 直播 / 解析全覆盖；
6. **相对路径基准**：多数上游的 `./x.js` 相对配置文件目录，但 `yw88075/tvbox/XBPQ/yw.json` 相对**仓库根目录**。拼错会生成 `XBPQ/XBPQ/xxx.json` 这类 404，导致整批爬虫节点首页空白。`sources.txt` 第三列可显式指定基准，脚本也会用 spider jar 自动探测；断言会拦截成批重复路径段。

## 本地运行

```bash
python scripts/build.py          # 本地只写 _local-*.json
python scripts/polish.py         # 只处理 _local-*.json（卫生 / 去重 / 消歧 / 排序）
python scripts/audit.py          # 复查报告 → reports/audit-latest.md
python scripts/home_probe.py     # 本地只写 _local-home*
python scripts/rank_home.py      # 本地只写 _local-*-homefirst.json
python scripts/variants.py       # 本地只写 _local-tvbox-*.json（三个场景版）
python scripts/assertions.py     # 校验 _local-* 或正式文件（可用环境变量切换）
python scripts/health.py         # 生成 site/index.html 预览健康页
python scripts/regression.py     # 全历史问题复核 → reports/regression-latest.md
python scripts/local_readonly.py # 把生成产物标记为本地只读（只需一次）
python scripts/serve.py          # 起自托管入口
```

仅依赖 Python 3 标准库，无需 pip 安装。想让本地写正式产物：设置 `ALLOW_LOCAL_WRITE=1`（并自行承担覆盖风险）。

## 免责声明

本项目仅做配置文件的聚合与格式修复，不托管、不存储任何视频内容。所有源均来自互联网公开仓库，版权归各自权利人所有。仅供技术学习研究，请于下载后 24 小时内删除。
