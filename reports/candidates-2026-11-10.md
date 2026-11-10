# 上游候选发现报告 · 2026-11-10

- 挖到 URL：222，评估：80，有边际贡献：9，拉取失败：71
- 基线：当前线上 `tvbox.json` 去重后 2133 个站点键
- 判定环境：github-actions（耗时 18s）

「新站点」= 相对线上配置去重后能新增的节点数，不是该源自身的站点总数。

| 新站点 | 其中采集型 | 直播 | 解析 | 源内站点 | URL | 来源 |
|---:|---:|---:|---:|---:|---|---|
| 70 | 34 | 1 | 24 | 151 | `https://gh-proxy.com/raw.githubusercontent.com/yw88075/tvbox/main/yw.json` | 多仓聚合 |
| 44 | 0 | 4 | 35 | 45 | `https://android.lushunming.qzz.io/json/index.json` | youhunwl/TVAPP |
| 42 | 9 | 4 | 0 | 48 | `https://dxawi.github.io/0/0.json` | Zhou-Li-Bin/Tvbox-QingNing,you |
| 28 | 4 | 0 | 0 | 33 | `https://gh-proxy.com/raw.githubusercontent.com//gaotianliuyun/gao/master/0827.json` | 多仓聚合 |
| 17 | 0 | 10 | 0 | 46 | `http://ztha.top/TVBox/thdjk.json` | Zhou-Li-Bin/Tvbox-QingNing |
| 0 | 0 | 7 | 13 | 80 | `https://9763.kstore.vip/aowu.json` | youhunwl/TVAPP |
| 0 | 0 | 2 | 4 | 63 | `http://tvbox.xn--4kq62z5rby2qupq9ub.top` | Zhou-Li-Bin/Tvbox-QingNing |
| 0 | 0 | 2 | 4 | 100 | `https://9280.kstore.vip/newwex.json` | youhunwl/TVAPP |
| 0 | 0 | 2 | 11 | 298 | `https://gh-proxy.com/raw.githubusercontent.com/gaotianliuyun/gao/master/js.json` | 多仓聚合 |

## 一键并入

```bash
python scripts/merge_candidates.py --take 5
```

会按顺序把前 N 个 URL 追加到 `sources.txt`（自动跳过已收录项），提交后次日构建生效。
