# TVBox 聚合配置（自动更新）

自动聚合多个公开 TVBox 配置源，去重、修复相对路径、每日存活复检后输出**一份**可直接使用的配置。

## 配置地址

```
https://raw.githubusercontent.com/dimogu/tvbox-merged/main/tvbox.json
```

国内直连 raw 不稳定时，用 jsDelivr 镜像（会自动回源缓存）：

```
https://cdn.jsdelivr.net/gh/dimogu/tvbox-merged@main/tvbox.json
```

TVBox / 影视仓 → 设置 → 配置地址，粘贴其中之一保存即可。

## 自动化机制

- **每天北京时间 04:00** 由 GitHub Actions 自动构建（`.github/workflows/update.yml`）
- 也可在 Actions 页面点 `Run workflow` 手动触发
- 构建流程：拉取上游 → 绝对化相对路径 → spider jar 下沉到站点 → 跨源去重 → 采集源 `?ac=list` 探活 → 剔除失效 → 提交
- 内容无实质变化时**不提交**，避免无意义的历史记录

## 上游源

| 来源 | 说明 |
|---|---|
| qist/tvbox (js / jsm / dianshi) | 主源，JS 爬虫型 |
| cyao2q/files (ok / xs / m) | 采集型 |
| noimank/tvbox (GitLab) | drpy 源 |
| gaotianliuyun/gao | 老牌源，与 qist 高度重合 |
| clun.top | 第三方，采集源质量较好 |

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
