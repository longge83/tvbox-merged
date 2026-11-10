# TVBox 聚合配置（自动更新 · 可观测 · 可回滚）

自动聚合多个公开 TVBox 配置源，去重、修复相对路径、内容过滤、每日存活复检、首页推荐探测、**端到端可播率探测**、多日稳定性判定与回归断言后，输出 7 份可直接使用的配置。每天北京时间 04:00 由 GitHub Actions 重建并提交。

> 功能之外，这套流水线还回答三个问题：**源到底能不能播**、**今天的判定可信吗**、**生成了坏版本怎么退回**。

---

## 一、配置地址（国内推荐直接复制整行链接）

按用途挑**一个**填进「设置 → 配置地址」。建议优先用带 `homefirst` 的版本：节点一个不少，但能出首页推荐的源被排到最前面。

| 文件 | 站点数（每日浮动） | 用途 |
|---|---|---|
| `tvbox-full-homefirst.json` | 约 1970 | **全量 + 首页优先**（前 ~1130 个可用），功能最全，依赖 spider jar |
| `tvbox-lite-homefirst.json` | 约 340 | **精简 + 首页优先**（前 ~115 个可用），不依赖 jar，低性能盒子首选 |
| `tvbox-network-homefirst.json` | 约 310 | **网络优化 + 首页优先**（前 ~115 个可用），不依赖境外代码托管，国内直连最稳 |
| `tvbox-home.json` | 约 1130 | 只保留首页可用节点（含同 jar 推断，推断项排在实测之后） |
| `tvbox.json` | 约 1970 | 全量版，原始顺序 |
| `tvbox-lite.json` | 约 340 | 精简版，原始顺序 |
| `tvbox-network.json` | 约 300 | 网络优化版，原始顺序 |

精确值看配置里的 `_meta.sites`、`_meta.home_first_count`，或打开[健康页](#四健康页与失败告警)。

### 国内主线路（jsDelivr，整行复制）

```
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-full-homefirst.json
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-lite-homefirst.json
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-network-homefirst.json
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-home.json
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox.json
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-lite.json
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-network.json
```

### 备用节点（主域名被污染 / 慢时，换前缀）

把上面链接里的 `cdn` 换成 `fastly` / `gcore` / `testingcf` 即可，例如：

```
https://fastly.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-full-homefirst.json
https://gcore.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox-lite-homefirst.json
```

### GitHub Pages（短 TTL，第二条独立线路）

Pages 的缓存时间远短于 jsDelivr 的 12 小时，而且不受 purge 影响，可作为主线路之外的第二入口：

```
https://dimogu.github.io/tvbox-merged/tvbox-full-homefirst.json
https://dimogu.github.io/tvbox-merged/tvbox-lite-homefirst.json
https://dimogu.github.io/tvbox-merged/tvbox-network-homefirst.json
https://dimogu.github.io/tvbox-merged/tvbox-home.json
https://dimogu.github.io/tvbox-merged/tvbox.json
https://dimogu.github.io/tvbox-merged/tvbox-lite.json
https://dimogu.github.io/tvbox-merged/tvbox-network.json
```

> 首次使用需在仓库 Settings → Pages 把 Build and deployment 的 Source 设为 **GitHub Actions**（一次性操作），之后每次构建自动部署。

### 官方源 raw（实时性最好，国内直连常需代理）

把上面 jsDelivr 链接换成 `https://raw.githubusercontent.com/dimogu/tvbox-merged/main/<文件名>` 即可。

### 固定稳定版（内容永不变化，可回滚）

每日构建会覆盖 `main` 上的文件；如果你希望**版本永远不变**，用 `stable.json` 里给出的 tag 链接：

```
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged@stable-YYYYMMDD/tvbox-full-homefirst.json
```

当前推荐的 tag 与全部版本的完整链接在 `stable.json`（[在线查看](https://raw.githubusercontent.com/dimogu/tvbox-merged/main/stable.json)），健康页也会直接列出。tag 只有**回归断言连续通过 3 天**才推进；要退回旧版：

```bash
python scripts/stable.py --rollback stable-20261109
```

### 关于 jsDelivr 缓存

jsDelivr 对分支引用返回 `Cache-Control: max-age=604800, s-maxage=43200`（CDN 边缘 12h、客户端 7 天），**且不会感知 commit 变化**。所以上面的国内链接都不带 `@main`（分支别名有独立的版本解析缓存，实测曾滞留上一轮构建）。Actions 每次提交后等待 30 秒、做两轮 purge。

加 `?xxx=随机数` 对 jsDelivr 的 GitHub 路径**无效**（缓存键不含 query）。若怀疑拿到旧配置，看顶部 `_meta.updated` / `_meta.sites`，或手动访问 `https://purge.jsdelivr.net/gh/dimogu/tvbox-merged/tvbox.json`。

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
- 每个 `csp_*` 站点必须有 http `jar`
- 无残留 `./` `../`、无成批 `XBPQ/XBPQ/` 类重复路径段
- 首页版 ⊆ 全量版；三个 homefirst 版本站点数与对应源完全一致
- 过滤后站点 / 直播 / 解析均无违规关键词残留
- 抽样 15 个 jar（必须 200 + `PK` 魔数）与 15 个 ext，可用率不得低于 60%

**2. 归档与回滚**：`archive/YYYY-MM-DD/` 存最近 7 天的轻量备份（`manifest.json` + lite/home 的 gzip），完整版本由 git 历史与不可变 tag 回滚。

```
# 查看当前稳定版指针
cat stable.json

# 回滚到上一个 tag
python scripts/stable.py --rollback stable-20261109
```

**3. 生成产物只由 Actions 提交**：所有 `tvbox*.json`、`health.json`、`quality.json`、`stable.json`、`home-sites.txt`、`history/`、`archive/` 都在 `.gitignore` 里（新文件不会被本地误提交），workflow 用 `git add -f` 独家提交。**已跟踪的旧配置不做 `git rm --cached`**：它们本身就是回退基线，一旦从索引移除，某天断言失败 Actions 不提交时线上直链会直接 404，连上一版都没得退。本地执行 `python scripts/local_readonly.py` 给这些文件加 `skip-worktree`，`git status` 便永远干净，不会再出现本地推送反复 pull/merge 冲突（`--status` 查看、`--undo` 解除）。

**4. 本地试跑保护**：非 Actions 环境下，所有脚本自动把输出改成 `_local-*.json`，绝不覆盖线上配置（曾因本地试跑把 lite 版覆盖成单源版）。确实要写正式文件时才设 `ALLOW_LOCAL_WRITE=1`。

---

## 六、上游可持续与体积治理

- `sources.txt` 存 29 个源（每行 `名称|URL[|相对路径基准]`，`#` 为注释），改文件提交即可增删，**不用改代码**。单个源失效只跳过它。
- **周级候选发现**（`.github/workflows/discover.yml`）：每周一自动跑 `scripts/discover.py`，挖导航仓库 README / 仓库 tree / 多仓聚合，相对**线上真实配置**算「去重后边际贡献」，产出 `reports/candidates-YYYY-MM-DD.md` 与 `candidates.json`。人工看一眼后一键并入：

  ```bash
  python scripts/merge_candidates.py --take 5 --dry-run   # 先看会加什么
  python scripts/merge_candidates.py --take 5             # 追加到 sources.txt
  ```

- **关键词外置**：`blocklist.txt`（每行一个正则，改文件即生效），同时扫站点名 / api / ext / jar、直播、解析，不再只扫站点名。
- **parses 去重**：按归一化 URL 去重，330 个解析通道里的重复项不再重复下发；`_meta.parses_deduped` 记录数量，另抽样验证解析通道是否真的 200。
- **jar 分级 + 网络优化版**：`_meta.jar_count` / `jar_top` 统计依赖，`tvbox-network.json` 只保留不依赖境外代码托管 jar/ext 的 http 站点，给低端盒子与国内直连场景。
- **自托管入口**（合规 / 内网备用）：在 NAS、软路由或家里一台常开机器上跑，局域网设备填内网地址，避免公开仓库被投诉下架后所有设备同时失效：

  ```bash
  python scripts/serve.py --port 8080 --refresh 21600
  # 设备填：http://192.168.1.10:8080/tvbox-lite-homefirst.json
  ```

  上游拉不到时继续用上次成功缓存，绝不返回空配置；提供 `/` 状态页、`/health`、ETag 与 CORS。

---

## 七、自动化机制

- **每天北京时间 04:00** 由 `.github/workflows/update.yml` 构建；**每周一**由 `discover.yml` 找新源；两者都支持手动 `Run workflow`。
- 主流程：`build.py` → `home_probe.py` → `recheck.py` → `play_probe.py` → `rank_home.py` → **`assertions.py`（失败即停）** → `history.py` → 提交配置与历史 → `stable.py`（归档 + 不可变 tag）→ `health.py` → 上传 Pages artifact → 提交归档与指针 → push → 两轮 purge CDN → 部署 Pages。
- 内容无实质变化时不写文件；判定历史每天仍会留痕，便于看趋势。
- 首页探测、误杀复核、可播率探测为 `continue-on-error`（单点网络抖动不影响主配置产出），只有**回归断言是硬阻断**。

## 合并时处理的坑

1. **相对路径**：上游大量使用 `./js/x.js`、`./jar/x.jar`，合并后必须改写为绝对 URL；
2. **spider jar 冲突**：一份配置只能有一个全局 spider，不同源依赖不同 jar，因此把各源原配 jar 写入站点级 `jar`；
3. **重复**：按「归一化名称 + 脚本标识」和「相同 http api」双重去重；
4. **失效源**：连续 3 天探活失败才剔除，次日恢复即重新纳入；
5. **内容过滤**：外置关键词，站点 / 直播 / 解析全覆盖；
6. **相对路径基准**：多数上游的 `./x.js` 相对配置文件目录，但 `yw88075/tvbox/XBPQ/yw.json` 相对**仓库根目录**。拼错会生成 `XBPQ/XBPQ/xxx.json` 这类 404，导致整批爬虫节点首页空白。`sources.txt` 第三列可显式指定基准，脚本也会用 spider jar 自动探测；断言会拦截成批重复路径段。

## 本地运行

```bash
python scripts/build.py          # 本地只写 _local-*.json
python scripts/home_probe.py     # 本地只写 _local-home*
python scripts/rank_home.py      # 本地只写 _local-*-homefirst.json
python scripts/assertions.py     # 校验 _local-* 或正式文件（可用环境变量切换）
python scripts/health.py         # 生成 site/index.html 预览健康页
python scripts/local_readonly.py # 把生成产物标记为本地只读（只需一次）
python scripts/serve.py          # 起自托管入口
```

仅依赖 Python 3 标准库，无需 pip 安装。想让本地写正式产物：设置 `ALLOW_LOCAL_WRITE=1`（并自行承担覆盖风险）。

## 免责声明

本项目仅做配置文件的聚合与格式修复，不托管、不存储任何视频内容。所有源均来自互联网公开仓库，版权归各自权利人所有。仅供技术学习研究，请于下载后 24 小时内删除。
