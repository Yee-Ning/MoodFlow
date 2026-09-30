# 搜索模块修复、检查与云端发布

## 问题与修复

线上已复现 `kanye`、`Kanye West` 的歌曲分类搜索返回 502；同一关键词的前 8 条正常，50 条失败。

网易云 `/cloudsearch` 第 15 条歌曲 `EVERYBODY`（真实 ID `2148728095`）的 `al.name` 为空、`al.id=0`。原 Java 实体转换把专辑名称作为必填字段，一条不完整的专辑元数据导致整批歌曲失败。

- 缺少专辑元数据时保留歌曲的真实 ID、名称、歌手、时长和可用封面，专辑字段返回空字符串，前端显示“未知专辑”。
- 只有专辑 ID 大于 0 且名称非空时，才输出专辑引用和 `metadata.album_id`；歌手引用同样要求有效 ID、名称，不编造实体。
- 歌曲详情、专辑曲目、歌手详情和相似歌曲的引用使用相同的可选专辑处理；空歌手名称不破坏正常歌曲，无法识别的歌手不会产生引用。
- 歌曲核心 ID 必须大于 0，歌曲名称仍然必填。参数错误、真正的上游失败继续返回明确错误，没有用空结果掩盖失败。
- 前端加强关联实体和专辑详情校验。
- 搜索重试通过页面 effect 发起，保留请求取消流程；切换分类或离开页面时，重试请求也会被取消。

功能提交：`3a11237cbce78ffb1e8ce6dc31db681c1430e360`。没有新增依赖、数据库迁移或 MainGraph 改动。

## 修改文件

| 文件 | 作用 |
| --- | --- |
| `music-backend/src/main/java/cn/itcast/demo/musicbackend/service/NeteaseMusicService.java` | 可选音乐元数据转换、有效实体引用、歌曲核心 ID 校验。 |
| `music-backend/src/test/java/cn/itcast/demo/musicbackend/service/MusicSearchMetadataTests.java` | 6 项服务和 MVC 回归测试，覆盖 50 条真实问题形态、元数据边界、身份校验、请求错误和上游失败。 |
| `soul-music-room/src/api/musicSearch.ts` | 统一验证搜索实体、关联引用和专辑详情。 |
| `soul-music-room/src/pages/SearchPage.tsx` | 重试纳入 effect 生命周期。 |
| `soul-music-room/scripts/test-music-search.mjs` | 新增 50 条缺专辑歌曲、取消重试、无效引用和专辑数据回归。 |

本报告、桌面/手机截图及 `search-repair-assets-20260930/public-checks.json` 记录验收证据。

## 自动化验证

| 目录 | 命令 | 结果 |
| --- | --- | --- |
| `music-backend` | `mvn -B verify`（JDK 21） | 75 项测试通过，0 失败、0 跳过；JAR 构建通过。 |
| `soul-music-room` | `npm run test:search` | 搜索 API、分类、渲染、缺失元数据、队列、校验、错误隔离、取消与重试检查通过。 |
| `soul-music-room` | `npm run test:ai-layout` | 通过。 |
| `soul-music-room` | `npm run test:ai-stream` | 通过。 |
| `soul-music-room` | `npm run test:music-assets` | 通过。 |
| `soul-music-room` | `npm run test:ai-trigger` | 通过。 |
| `soul-music-room` | `npm run test:worldline` | flow、view 均通过。 |
| `soul-music-room` | `npm run lint` | 通过，无静态检查错误或警告。 |
| `soul-music-room` | `npm run build` | TypeScript、Vite 生产构建通过；保留已有大于 500 kB 的 chunk 提示。 |
| `ai-service` | `.venv/Scripts/python.exe -m pytest tests/test_music_tool.py tests/test_music_asset_tools.py tests/test_tool_calling_subgraph.py tests/test_worldline_music_catalog.py -q` | 58 项测试、10 项子测试通过。 |
| 项目根目录 | 当前任务文件的 `git diff --check` | 通过。原有无关用户改动未暂存或修改。 |

回归测试在修复前复现了 502 和缺失元数据异常，修复后保持原断言通过，没有删除或弱化测试。

## 公网 API 检查

在严格 TLS 校验、未登录的请求中检查 22 条接口路径，全部符合预期；返回实体同时通过 AI 层 Pydantic `MusicEntity` 校验。

- `kanye`、周杰伦的歌曲/歌手/专辑/歌单各返回 50 条；`Kanye West` 的歌曲分类返回 50 条。
- `EVERYBODY` 保留真实歌曲 ID，无无效专辑引用；原 `/api/music/search` 的 50 条歌曲查询兼容。
- 两首歌曲详情、问题歌曲实体详情、歌手实体详情和专辑完整详情成功。
- 空关键词、非法分类、超过 50 条限制、专辑 ID 0 返回 400。
- 未登录访问音频地址和歌单详情返回 401。
- 实际无匹配关键词返回 200、空数组。

详细路径与状态见 `search-repair-assets-20260930/public-checks.json`。

## 浏览器交互与视觉检查

在已发布的公网页面执行：

- 直接进入歌曲分类显示 50 条，无错误；第 15 条歌曲显示“未知专辑”。
- 点击歌手分类中的 Kanye West，再进入其歌曲，显示 50 条，无错误。
- 快速点击歌手、歌曲分类，旧请求没有覆盖新分类结果。
- 播放按钮在未登录状态显示登录提示；加入 `EVERYBODY` 到队列后全局播放器显示 1 首，未开始播放。
- 专辑分类显示真实结果；`My Beautiful Dark Twisted Fantasy` 展开完整 14 首歌曲，关闭预览正常。
- 歌单结果打开真实 ID `2913612358`，显示“请先登录网易云音乐”；返回后保留关键词和分类。
- 超过 200 个字符的关键词显示参数错误，重试按钮可用；修改关键词并提交后恢复正常结果。
- 回车提交、刷新后的 URL 关键词、顶部输入和分类一致。
- 实际无匹配词显示“没有找到相关音乐”；清空关键词后恢复搜索引导，不残留旧结果。全部分类展示四类各 8 条，共 32 条，无分类错误。
- 桌面 1280×720、手机 390×844 均展示 50 条歌曲；页面宽度与 scrollWidth 相同，没有横向溢出，分类及搜索控件可操作。

截图：`search-repair-assets-20260930/desktop.jpg`、`search-repair-assets-20260930/mobile.jpg`。

验收结束后已恢复浏览器视口并关闭临时标签页。

## 云服务器更新

沿用 `deploy/update.sh`，先 dry-run，再正式发布。

- 网站：<https://47.83.162.191/search?q=kanye&type=song>。
- 服务端 HEAD：`3a11237cbce78ffb1e8ce6dc31db681c1430e360`，工作区干净。
- 发布完成时间：2026-09-30 23:02:54（Asia/Shanghai）。
- 仅重建 `java`、`frontend`，没有迁移文件变更。
- 部署记录：`/secure/moodflow/deployments/20260930T150035Z-3a11237c.json`，`status=passed`。
- 备份记录：`/secure/moodflow/backups/manifests/20260930T150036Z-3a11237c.json`，`status=complete`、`restore_verified=true`。
- Java、前端、AI、NetEase、PostgreSQL、Qdrant 全部健康；既有 migrate 容器退出码 0。
- 首页、认证状态、健康接口、SSE、Worldline 发布检查通过，Worldline 返回 9 个节点。

## 验证范围与限制

- 本轮覆盖搜索模块及其音乐接口、播放器入口，并运行完整 Java 测试、现有全部前端检查和相关 AI 调用方测试；没有重跑全项目的历史离线语料评测。
- 未提供真实网易云登录会话，本轮未验证实际音频播放、私有歌单内容和云端写入；登录限制、未登录提示、队列操作与相应自动化检查已验证。
- 搜索沿用每类最多 50 条；歌手入口依照关键词相关性检索，现有服务未提供完整作品分页接口。
- 网易云的歌曲核心身份完全缺失或 API 本身失败时，仍会显示服务错误；本次只兼容缺失的可选元数据。
