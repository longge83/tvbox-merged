# TVBox 聚合配置（自动更新）

自动聚合多个公开 TVBox 配置源，去重、修复相对路径、每日存活复检后输出**一份**可直接使用的配置。

## 配置地址

**全量版**（约 1900+ 站点，功能最全，依赖 spider jar，适合性能较好的设备）：

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/tvbox.json
```

**精简版**（约 300 个 http 站点，采集源均已探活，不依赖 spider jar，适合低性能盒子）：

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/tvbox-lite.json
```

国内直连 raw 不稳定时，用 jsDelivr 镜像（有缓存，非实时）：

```
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged@main/tvbox.json
```

TVBox / 影视仓 → 设置 → 配置地址，粘贴其中之一保存即可。

## 上游源

`sources.txt` 存放上游清单（每行 `名称|URL`，`#` 为注释），改这个文件提交即可增删源，**不用改代码**。目前收录 **28 个源**，来自 GitHub 仓库、GitLab、以及第三方直连线路；全部经过连通性、JSON 结构与站点抽样探活检验。

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

## 上游源

`sources.txt` 存放上游清单（每行 `名称|URL`，`#` 为注释），改这个文件提交即可增删源，**不用改代码**。目前收录 **28 个源**，来自 GitHub 仓库、GitLab、以及第三方直连线路；全部经过连通性、JSON 结构与站点抽样探活检验。

## 合并时处理的坑

1. **相对路径**：上游大量使用 `./js/x.js`、`./jar/x.jar`，只在原作者仓库下能解析，合并后必须改写为绝对 URL；
2. **spider jar 冲突**：一份配置只能有一个全局 spider，不同源依赖不同 jar，因此把各源原配 jar 写入站点级 `jar` 字段；
3. **重复**：qist 与 gao 的站点几乎完全重合，按「归一化名称 + 脚本标识」和「相同 http api」双重去重；
4. **失效源**：每天对 CMS 采集接口探活，失败即剔除（次日若恢复会重新纳入）。

## 本地运行

```bash
python scripts/build.py          # 生成 tvbox.json
```

仅依赖 Python 3 标准库，无需 pip 安装。

## 免责声明

本项目仅做配置文件的聚合与格式修复，不托管、不存储任何视频内容。所有源均来自互联网公开仓库，版权归各自权利人所有。仅供技术学习研究，请于下载后 24 小时内删除。
