# TVBox 聚合配置（自动更新）

自动聚合多个公开 TVBox 配置源，去重、修复相对路径、每日存活复检后输出**全量版与精简版**两份可直接使用的配置。

## 配置地址

**全量版**（约 1900+ 站点，功能最全，依赖 spider jar，适合性能较好的设备）：

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/tvbox.json
```

**精简版**（约 340 个 http 站点，采集源均已探活，不依赖 spider jar，适合低性能盒子）：

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/tvbox-lite.json
```

国内直连 raw 不稳定时，用 jsDelivr 镜像（把 `tvbox.json` 换成 `tvbox-lite.json` 即为精简版）：

```
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged@main/tvbox.json
```

备用 CDN 端点（同一份内容，节点不同，主域名异常时可换）：

```
https://fastly.jsdelivr.net/gh/dimogu/tvbox-merged@main/tvbox.json
https://gcore.jsdelivr.net/gh/dimogu/tvbox-merged@main/tvbox.json
```

TVBox / 影视仓 → 设置 → 配置地址，粘贴其中之一保存即可。

### 关于 jsDelivr 缓存

jsDelivr 对 `@main` 这类**分支引用**返回的响应头是 `Cache-Control: max-age=604800, s-maxage=43200`，即 CDN 边缘缓存 12 小时、客户端缓存 7 天，**且不会感知 commit 变化**。所以镜像有可能比 raw 慢一拍，各边缘节点也可能不同步（曾出现主域名还是旧配置、fastly 已是新配置的情况）。

为此 Actions 在每次构建提交后会调用 jsDelivr 的 `purge` 接口主动清缓存，正常情况下镜像与 raw 同步。若怀疑拿到旧配置，打开链接看顶部 `_meta.updated` 和 `_meta.sites`，与 raw 版本比对即可；需要手动强刷可访问：

```
https://purge.jsdelivr.net/gh/dimogu/tvbox-merged@main/tvbox.json
```

注意加 `?xxx=随机数` 之类的查询参数对 jsDelivr 的 GitHub 路径**无效**（缓存键不含 query），别用这个办法破缓存。

## 首页推荐版（解决「切到某些节点后首页一片空白」）

TVBox 的**主界面推荐**和**搜索**是两套数据，能搜到片子不代表能出首页：

| 站点类型 | 首页数据从哪来 | 常见的空白原因 |
|---|---|---|
| CMS 采集源 (`type=1`) | `?ac=videolist` / `?ac=detail` / `?ac=list` 返回的影片列表 + 分类 | 只返回分类不返回影片、接口超时、站点只做搜索源 |
| `csp_*` 爬虫源 (`type=3`) | jar 里的 `homeContent()`，分类规则常在 `ext` 指向的 JSON | jar 404 / 不是 jar / ext 首页配置文件失效 |

`scripts/home_probe.py` 每天在云端逐站探测，判定等级：

| 等级 | 判定依据 | 纳入首页版 |
|---|---|---|
| `ok` | CMS 首页影片列表实测非空（记录条数与命中的 `ac` 参数） | ✅ 排最前 |
| `likely` | jar 有效且 ext 首页配置有效 | ✅ |
| `classes_only` | 只返回分类、没返回影片，首页可能空 | ✅ 排最后 |
| `unknown` | 无 ext、依赖 jar 内置首页，HTTP 无法判定 | ❌ 需客户端实测 |
| `fail` | jar 404/非 jar、ext 失效、接口连不上 | ❌ |

**首页版配置**（只保留上表中打勾的节点，按可靠度排序）：

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/tvbox-home.json
```

**可读清单**（每行：`等级 | 站点名 | 判定依据`，适合人眼挑节点）：

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/home-sites.txt
```

国内访问同样可套 `cdn.jsdelivr.net/gh/dimogu/tvbox-merged@main/` 前缀，每日构建后会自动清 CDN 缓存。

> 坑：不少作者把 jar 伪装成 `.jpg` / `.png` 传到图床（实测返回 `PK\x03\x04` ZIP 魔数）。判断 jar 是否有效**只能看内容**，按后缀判会误杀几百个节点。

## 上游源

`sources.txt` 存放上游清单（每行 `名称|URL`，`#` 为注释），改这个文件提交即可增删源，**不用改代码**。目前收录 **29 个源**，来自 GitHub 仓库、GitLab、以及第三方直连线路；全部经过连通性、JSON 结构与站点抽样探活检验。

| 类别 | 代表源 |
|---|---|
| 老牌主源 | qist/tvbox、cyao2q/files、gaotianliuyun/gao、noimank(GitLab) |
| 采集型聚合 | yw88075、davidlee6628/ssr、HowKongFish、juhe-subhub、chitue/dongliTV |
| 影视仓镜像 | Lightconer/tvbox-ysc-config、apaodh/apaodh backups |
| 第三方直连 | clun.top、fish2018、jundie.top |

构建时每个源独立拉取，单个源失效只跳过它，不影响整体。

## 自动化机制

- **每天北京时间 04:00** 由 GitHub Actions 自动构建（`.github/workflows/update.yml`）
- 也可在 Actions 页面点 `Run workflow` 手动触发
- 构建流程：读取 `sources.txt` → 并行拉取上游 → 绝对化相对路径 → spider jar 下沉到站点 → 跨源去重 → 内容过滤（剔除成人/违规站点）→ 采集源 `?ac=list` 探活 → 剔除失效 → 输出全量版 + 精简版 → 提交
- 内容无实质变化时**不提交**，避免无意义的历史记录
- 提交后自动清除 jsDelivr CDN 缓存，避免镜像滞留旧配置

## 合并时处理的坑

1. **相对路径**：上游大量使用 `./js/x.js`、`./jar/x.jar`，只在原作者仓库下能解析，合并后必须改写为绝对 URL；
2. **spider jar 冲突**：一份配置只能有一个全局 spider，不同源依赖不同 jar，因此把各源原配 jar 写入站点级 `jar` 字段；
3. **重复**：qist 与 gao 的站点几乎完全重合，按「归一化名称 + 脚本标识」和「相同 http api」双重去重；
4. **失效源**：每天对 CMS 采集接口探活，失败即剔除（次日若恢复会重新纳入）；
5. **内容过滤**：按站点名与 API 关键字剔除成人/违规资源，避免公开聚合配置夹带不适宜内容；
6. **相对路径基准**：多数上游的 `./x.js` 相对配置文件目录，但也有上游（如 `yw88075/tvbox/XBPQ/yw.json`）相对**仓库根目录**。拼错会生成 `XBPQ/XBPQ/xxx.json` 这类 404 路径，导致整批爬虫节点首页空白。`sources.txt` 第三列可显式指定基准，脚本也会用 spider jar 自动探测正确基准。

## 本地运行

```bash
python scripts/build.py          # 生成 tvbox.json（全量）与 tvbox-lite.json（精简）
```

仅依赖 Python 3 标准库，无需 pip 安装。`scripts/` 下另有 `harvest.py`（挖接口）、`scan_repos.py`（扫仓库）、`eval_sources.py`（算边际贡献），用于后续继续扩容。

## 免责声明

本项目仅做配置文件的聚合与格式修复，不托管、不存储任何视频内容。所有源均来自互联网公开仓库，版权归各自权利人所有。仅供技术学习研究，请于下载后 24 小时内删除。
