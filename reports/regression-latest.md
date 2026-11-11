# 全历史问题回归复核 · 2026-11-11

把九轮迭代里真实出现过的故障逐条复核，确认修复仍在、防线没被绕过。
默认离线，`--online` 追加线上线路比对；判定环境：github-actions。

**结果：39 项检查，37 项通过，2 项未通过（其中阻断 2 项），0 项跳过 —— 存在需处理项**

## 产物契约

| # | 历史出处 | 复核项 | 结果 | 证据 |
|---|---|---|---|---|
| A01 | 第九轮 / 全程 | 10 份配置齐全、合法 JSON 且无重复键 | ✅ 通过 | 10 份全部合法、无重复 key |
| A02 | 第九轮：导入报 IllegalStateException | style / header 不得是 JSON 对象 | ✅ 通过 | 0 处对象型 style/header（不会抛 IllegalStateException） |
| A03 | 第九轮 / 第三轮 | 数字字段不得塞字符串 / 直播 type 必须是整数 | ❌ 未通过 | 980 处数字字段是字符串：tvbox.json lives:type='0', tvbox.json lives:type='0', tvbox.json lives:type='0' |
| A04 | 第九轮：播放期 NullPointerException(String.trim()) | 裸 trim 的 String 字段不得缺失 / null / 非字符串 | ✅ 通过 | 客户端会裸 trim() 的字段全部非 null（不会 NPE 闪退） |
| A05 | 第六轮：图片被当 spider jar（352 站） | jar 不得是图片 / 媒体 URL | ✅ 通过 | 0 个图片/媒体被塞进 jar 槽 |
| A06 | 第五轮：jar 字段带前后空格（26 站） | jar 值必须已 strip | ✅ 通过 | jar 值无前后空白（不会被误判缺 jar / 拉 404） |
| A07 | 第一轮：csp 爬虫节点没有 jar | csp 站点必须有 http jar | ✅ 通过 | 367/1754 个 csp 站点缺 jar（上游本身可能没写，阈值 30%） |
| A08 | 合并坑：上游相对路径未绝对化 | api/jar/ext/url 不得是相对路径 | ✅ 通过 | 0 处 ./ ../ 相对路径 |
| A09 | 第九轮：yw88075 相对仓库根拼错（138 站首页空白） | 无相邻重复路径段（跳过 raw 的 owner/repo 同名） | ✅ 通过 | 0 处 XBPQ/XBPQ 类基准拼错 |
| A10 | 第八轮：换皮名 / raw-jsdelivr-gh-proxy 别名重复 | 站点 / 直播 / 解析按内容指向去重 | ✅ 通过 | 10 份配置按内容指向判重后均无重复 |
| A11 | 第三轮 / 本轮：筛选版照抄全量版 meta | _meta 计数与实际内容一致 | ✅ 通过 | sites/lives/parses/jar_count 全部按各自内容重算 |
| A12 | 第三轮：精简版号称不依赖 jar 却带 spider + 6 个 jar 站 | 精简 / 网络 / 极速版真的不加载 jar | ✅ 通过 | 5 份「无 jar 依赖」版本实测 0 jar / 0 csp / 无全局 spider |
| A13 | 第四轮：境外托管黑名单口径（gitee 误伤） | 网络 / 极速版 sites 无境外配置托管地 | ✅ 通过 | 网络优化版 / 国内极速版的 sites 均无境外配置托管地（lives 沉底不删属设计） |
| A14 | 场景版 / 首页版混入非全量版节点 | 派生版本站点集合 ⊆ 全量版 | ✅ 通过 | 9 份派生配置均 ⊆ 全量版（按内容指向，改名不影响） |
| A15 | 第三轮：8 个分隔符被当站点 / AList×3 重名 / 客户端不认字段 | 成品卫生：api、名称、主键、解析 url、外来字段 | ✅ 通过 | 无空/非法 api、重名、重复 key、非法解析 url、外来字段 |
| A16 | 第九轮：1.14 MB 大配置在 22 KB/s 下超时 | 配置为紧凑 JSON（体积不超过紧凑序列化 105%） | ✅ 通过 | 10 份均为紧凑 JSON（慢网络拉取超时风险已降） |
| A17 | 本轮：lives/parses 被清空时 meta 记 0、断言 0==0 放行 | 直播 / 解析集合不得为空 | ✅ 通过 | 10 份配置的 lives / parses 均非空 |

## 构建链路

| # | 历史出处 | 复核项 | 结果 | 证据 |
|---|---|---|---|---|
| B01 | 第三轮：polish 必须跑在 home_probe 之前（下标回填） | workflow 顺序 polish → home_probe | ✅ 通过 | polish 在探测之前 |
| B02 | 本轮：git add -f 整条 + \|\| true 会静默吞掉当天全部提交 | workflow 提交步骤逐文件判断且不吞错误 | ✅ 通过 | 逐文件 git add=True / 无静默吞失败=True / 必需产物显式检查=True |
| B03 | 第十轮：bash -e 末轮 `[ ... ] && sleep` 返回 1 导致 purge 跳过 | workflow 无 && sleep 末表达式 | ✅ 通过 | 已改为 if/exit 0 结构 |
| B04 | 第九轮：jsDelivr @main 顽固缓存 / purge 需多轮 | purge 同时清两种路径且 workflow 至少 2 轮 | ✅ 通过 | 无版本号 + @main 双路径 purge；--attempts 3 |
| B05 | 第八轮：stable tag 里没有新增文件却造出 404 链接 / 连续 3 天才推进 | stable.py 用 git cat-file 判文件、min-streak 3 | ✅ 通过 | tag_contains 用 git cat-file -e 判文件存在 + 3 天连胜门槛均在位 |
| B06 | 第十一轮：同一天多次运行 history 覆盖快照 | history.py 按日期存快照、连胜按天计 | ✅ 通过 | 快照按 date 命名，streak 按天数 |
| B07 | 第九轮：choose_base 第三列修相对仓库根的路径 | sources.txt 格式合法且源数量未塌 | ✅ 通过 | 29 个上游源，格式异常 0 条 |
| B08 | 第十轮：关键词外置 / 生成物本地只读 | blocklist 非空、.gitignore 覆盖、local_readonly 在位 | ✅ 通过 | blocklist / .gitignore / local_readonly.py 三项齐全 |
| B09 | 各轮修复必须有硬断言守着，不能只靠文档 | assertions.py 覆盖全部关键回归防线 | ✅ 通过 | 13 条关键硬断言全部在位 |
| B10 | 每日保险丝结果 | 当日回归断言已通过 | ✅ 通过 | passed=True，28 项检查，失败 [] |
| B11 | 第十轮：误杀率 52% 证明单日判定不可信 | quality.json 保留多日历史与抽样基线 | ✅ 通过 | 多日历史 / 连败 / 抽样基线字段齐全（2 天） |
| B12 | 第九轮：README 要求 Pages 主线路；健康页给无版本 CDN 链接 | README 主线路 Pages / health.py 直链带 @main | ❌ 未通过 | README 主推 Pages、健康页两列直链均带 @main，口径一致 |
| B13 | 本轮：健康页 </table> 写在 for 循环体内（线上 33 闭合 / 5 开标签） | 生成的健康页 HTML 表格标签平衡 | ✅ 通过 | 源码静态检查：6 个表格闭合均在循环外 |
| B14 | 每轮交付前的基础检查 | 全部 scripts/*.py 可编译 | ✅ 通过 | 23 个脚本全部编译通过 |
| B15 | 本轮：tag 复用时指针 commit 写成当前 HEAD、与不可变 tag 不一致 | stable.json 的 commit 指向 tag 实际提交 | ✅ 通过 | tag=stable-20261110 / 指针 commit=4e83a87b59 / tag 实际=4e83a87b59 |
| B16 | 本轮：lives/parses 半量兜底误伤设计内解析截断（已被断言拦下） | polish 的集合兜底只在被清空时触发 | ✅ 通过 | 兜底只判「清空」，不会把 parse_cap 的配额截断当异常回退 |
| B17 | 本轮：上游改版返回 JSON 数组让整个每日构建崩溃 | build.py 对非对象 JSON 与非法 URL 有兜底 | ✅ 通过 | 非 dict JSON 跳过该源 + fetch 先 percent-encode，单源坏数据不再拖垮整天构建 |
| B18 | 本轮：`or len(body) > 0` 让可播率判定完全失效（虚高） | play_probe 媒体判定排除 HTML | ✅ 通过 | 魔数白名单 + HTML 黑名单，不再「任何非空 200 都算媒体」 |
| B19 | 本轮：反爬源 200+HTML 被当成「复活」，死源永不淘汰 | recheck 复活判定剔掉 HTML 页面 | ✅ 通过 | 复活兜底排除 HTML 错误页，死源能攒够连败被淘汰 |
| B20 | 本轮：serve.py 无 etag 时普通 GET 也回 304、默认上游无 @main、缺三个场景版 | 自托管入口三处修复在位 | ✅ 通过 | 304 判定有 etag 前置 / 默认上游带 @main / FILES 含三个场景版 |
| B21 | 本轮：断言失败的缺快照日被跳过、stable 3 天门槛可被绕过；两工作流 push 互撞 | history 连胜日期连续性 + workflow push 前 rebase | ✅ 通过 | 连胜按日历日连续累计；CI push 前先 rebase，两个工作流不再互拒 |
| B22 | 本轮：README 推荐的 gcore / 健康页推荐的 fastly 是 12h 缓存盲区 | 推荐镜像滞后可被发现 | ✅ 通过 | purge 记录 fastly/gcore 滞后；健康页自检覆盖 gcore 线路 |

## 未通过明细

- **A03 数字字段不得塞字符串 / 直播 type 必须是整数**（阻断）：980 处数字字段是字符串：tvbox.json lives:type='0', tvbox.json lives:type='0', tvbox.json lives:type='0'
- **B12 README 主线路 Pages / health.py 直链带 @main**（阻断）：README 主推 Pages、健康页两列直链均带 @main，口径一致

---

本报告由 `scripts/regression.py` 生成，与 `assertions.py` 的每日保险丝互补：断言只决定「今天能不能提交」，本报告回答「历史修过的东西有没有悄悄坏掉」。
