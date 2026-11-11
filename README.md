# TVBox 聚合配置

自动聚合多个公开 TVBox 配置源，去重（含同一采集站的多镜像归并）、修复、每日复检后，输出 10 份可直接使用的配置。每天自动更新。

## 一、配置地址（链接与含义）

在客户端「设置 → 配置地址」里，**挑一个**填进去即可。

不知道选哪个：先用 `tvbox-quality.json`（精播版）；老盒子 / 国内直连用 `tvbox-cn-fast.json`；想长期不动用 `tvbox-stable7.json`；想一个节点都不少用带 `homefirst` 的版本。

| 文件 | 含义（用途） | 站点数（每日浮动，仅供参考） |
|---|---|---|
| `tvbox-quality.json` | **精播版**：只留实测可播 / 首页有影片 / jar 有效的节点，宁缺毋滥 | ≈ 620 |
| `tvbox-cn-fast.json` | **国内极速版**：国内直连、无 spider jar、无爬虫源，开屏最快 | ≤ 120（本轮 120） |
| `tvbox-stable7.json` | **7 天稳定版**：近 7 天多次可用且当前无连败，不随上游抖动 | 视历史累积（本轮 200） |
| `tvbox-full-homefirst.json` | **全量 + 首页优先**，功能最全，依赖 spider jar | ≈ 2740，按首页实测可用性排序 |
| `tvbox-lite-homefirst.json` | **精简 + 首页优先**，真·不加载 jar，低性能盒子首选 | ≈ 980 |
| `tvbox-network-homefirst.json` | **网络优化 + 首页优先**，无 jar、无境外托管，国内直连最稳 | ≈ 290 |
| `tvbox-home.json` | 只保留首页可用节点（含同 jar 推断项） | ≈ 970 |
| `tvbox.json` | 全量版，按「可用 → 未知 → 降级」排序 | ≈ 2740 |
| `tvbox-lite.json` | 精简版，按「可用 → 未知 → 降级」排序 | ≈ 980 |
| `tvbox-network.json` | 网络优化版，按「可用 → 未知 → 降级」排序 | ≈ 290 |

## 二、怎么填链接

### 主线路（GitHub Pages，总是最新，推荐）

缓存只有几分钟、每次构建自动部署、不受 CDN 边缘缓存影响，国内一般也能直连。把上一节的文件名替换进统一前缀即可：

```
https://dimogu.github.io/tvbox-merged/<文件名>
```

> ⚠️ 这些文件都是**点播（VOD）配置**，填进客户端的「**点播**」地址框即可。配置内已自带直播源（`lives` 数组），导入后客户端会**自动加载直播**，无需、也不建议再单独往「直播」框填链接。配置每天自动重建，**彻底退出 app 再打开就会联网重新拉取，无需重新导入**；从「设置」页按返回键回首页会读本地缓存，想立刻生效请从多任务里划掉 app。若曾报 `IllegalStateException / 配置取得失败`，可换一个文件名或清空 app 数据后重试。

例如：

```
https://dimogu.github.io/tvbox-merged/tvbox-quality.json
https://dimogu.github.io/tvbox-merged/tvbox-cn-fast.json
https://dimogu.github.io/tvbox-merged/tvbox-stable7.json
https://dimogu.github.io/tvbox-merged/tvbox-full-homefirst.json
https://dimogu.github.io/tvbox-merged/tvbox-lite-homefirst.json
https://dimogu.github.io/tvbox-merged/tvbox-network-homefirst.json
https://dimogu.github.io/tvbox-merged/tvbox-home.json
https://dimogu.github.io/tvbox-merged/tvbox.json
https://dimogu.github.io/tvbox-merged/tvbox-lite.json
https://dimogu.github.io/tvbox-merged/tvbox-network.json
```

> 首次使用需在仓库 Settings → Pages 把 Build and deployment 的 Source 设为 **GitHub Actions**（一次性操作），之后每次构建自动部署。

### 健康页（查看各版本完整直链与实时状态）

```
https://dimogu.github.io/tvbox-merged/
```

一页展示各版本站点数、完整直链、上游源健康、近 14 天趋势、可播率、回归状态、直播源实测（含河南台可播线路数）、稳定版固定链接。

## 三、直播 / 河南卫视

配置里的 `lives` 存的是**频道列表地址**，不是频道本身——客户端点进去才解析。所以「收了一百个直播源」并不代表能看：实测把 389 条河南台地址逐条拉到首个 TS 分片，只有 22 条真出画面，其余是组播内网地址（`/rtp/239.x`，只在当地运营商内网有效）、签名过期 403、死链，或整站频道都指向同一个「开通 VIP」提示视频。

因此每日构建会做三件事：

1. **实测**：每个列表拆成频道，河南台地址逐条往下拉一层到分片，只有真的下到 TS 码流才算可播；
2. **剔除收费 / 导流源**：一半以上频道是促销词、或某个提示视频被大量频道复用的，整条 list 从配置里删掉，并记进 `live_blocklist.txt`（构建阶段即拦截，不依赖当次探测）；
3. **自建专列表**：把实测可播的河南台线路汇成 [`live-henan.m3u`](https://dimogu.github.io/tvbox-merged/live-henan.m3u)，插到 `lives` 第一位，名称「河南台 · 自建校验」。同名频道按「·2」「·3」编号，客户端里逐条切换即可。

```
https://dimogu.github.io/tvbox-merged/live-henan.m3u
```

> 单条线路今天能播、明天可能 403，所以专列表刻意保留多条同名线路而不是只留一条；本轮没测到可播线路时保留上一版，绝不用空列表覆盖。上游把梨园频道错标成「河南卫视」的情况也做了纠正（`lypd` → 河南梨园频道）。

新增 / 下线直连直播源改 `live_sources.txt`，一行一个 `名称|URL`，下一次构建生效。

## 免责声明

本项目仅做配置文件的聚合与格式修复，不托管、不存储任何视频内容。所有源均来自互联网公开仓库，版权归各自权利人所有。仅供技术学习研究，请于下载后 24 小时内删除。
