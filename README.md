# TVBox 聚合配置

自动聚合多个公开 TVBox 配置源，去重、修复、每日复检后，输出 10 份可直接使用的配置。每天自动更新。

## 一、配置地址（链接与含义）

在客户端「设置 → 配置地址」里，**挑一个**填进去即可。

不知道选哪个：先用 `tvbox-quality.json`（精播版）；老盒子 / 国内直连用 `tvbox-cn-fast.json`；想长期不动用 `tvbox-stable7.json`；想一个节点都不少用带 `homefirst` 的版本。

| 文件 | 含义（用途） | 站点数（每日浮动，仅供参考） |
|---|---|---|
| `tvbox-quality.json` | **精播版**：只留实测可播 / 首页有影片 / jar 有效的节点，宁缺毋滥 | ≈ 655 |
| `tvbox-cn-fast.json` | **国内极速版**：国内直连、无 spider jar、无爬虫源，开屏最快 | ≤ 120（本轮 92） |
| `tvbox-stable7.json` | **7 天稳定版**：近 7 天多次可用且当前无连败，不随上游抖动 | 视历史累积（本轮 89） |
| `tvbox-full-homefirst.json` | **全量 + 首页优先**，功能最全，依赖 spider jar | ≈ 2960，前 ≈ 990 个实测可用 |
| `tvbox-lite-homefirst.json` | **精简 + 首页优先**，真·不加载 jar，低性能盒子首选 | ≈ 1210 |
| `tvbox-network-homefirst.json` | **网络优化 + 首页优先**，无 jar、无境外托管，国内直连最稳 | ≈ 520 |
| `tvbox-home.json` | 只保留首页可用节点（含同 jar 推断项） | ≈ 990 |
| `tvbox.json` | 全量版，按「可用 → 未知 → 降级」排序 | ≈ 2960 |
| `tvbox-lite.json` | 精简版，按「可用 → 未知 → 降级」排序 | ≈ 1210 |
| `tvbox-network.json` | 网络优化版，按「可用 → 未知 → 降级」排序 | ≈ 520 |

## 二、怎么填链接

### 主线路（GitHub Pages，总是最新，推荐）

缓存只有几分钟、每次构建自动部署、不受 CDN 边缘缓存影响，国内一般也能直连。把上一节的文件名替换进统一前缀即可：

```
https://dimogu.github.io/tvbox-merged/<文件名>
```

> ⚠️ 这些文件都是**点播（VOD）配置**，填进客户端的「**点播**」地址框即可。配置内已自带直播源（`lives` 数组），导入后客户端会**自动加载直播**，无需、也不建议再单独往「直播」框填链接。若之前导入报过 `IllegalStateException / 配置取得失败`，说明 app 缓存了旧配置——请**换一个文件名**（例如从 `tvbox-full-homefirst.json` 换成 `tvbox-quality.json`）或清空 app 缓存后重试。

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

一页展示各版本站点数、完整直链、上游源健康、近 14 天趋势、可播率、回归状态、稳定版固定链接。

## 免责声明

本项目仅做配置文件的聚合与格式修复，不托管、不存储任何视频内容。所有源均来自互联网公开仓库，版权归各自权利人所有。仅供技术学习研究，请于下载后 24 小时内删除。
