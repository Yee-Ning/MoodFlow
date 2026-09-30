# 搜索页面开发与验收

## 原因与改动

原 `/search` 使用 `StaticDestinationPage`，顶部输入框只通过路由 state 传递关键词，页面没有调用音乐检索接口。

现在 `/search` 使用真正的 `SearchPage`，复用已有 Spring Boot `GET /api/music/entities/search`，无需修改后端或 Agent 图结构。

- 支持歌曲、歌手、专辑、歌单，以及全部分类。
- 顶部回车或搜索按钮提交；`q`、`type` 写入 URL，支持刷新、分享和浏览器历史。
- 展示真实名称、封面、歌手、专辑、时长及歌单歌曲数量。
- 歌曲复用现有播放器播放或加入队列；未登录播放显示登录入口。
- 歌手结果可继续搜索其歌曲；专辑结果展开完整歌曲列表，可整张播放；歌单结果进入已有详情页。
- 提供加载、空关键词、空结果、分类失败与重试状态。
- 全部分类并行检索，单个分类失败保留其他结果；取消旧请求并拒绝旧结果覆盖新搜索。
- 检索参数与返回实体校验，React 正文转义，复用现有图片、API、认证与播放器封装，没有新增依赖。

## 修改文件

| 文件 | 职责 |
| --- | --- |
| `soul-music-room/src/pages/SearchPage.tsx` | 搜索结果、分类、队列、专辑歌曲预览和登录提示。 |
| `soul-music-room/src/pages/SearchPage.css` | 延续现有深色音乐页面视觉，增加桌面与移动端布局。 |
| `soul-music-room/src/api/musicSearch.ts` | 检索 API、参数/实体校验、分类错误隔离、专辑详情及播放器数据转换。 |
| `soul-music-room/src/store/searchStore.ts` | 搜索状态、取消和请求版本校验。 |
| `soul-music-room/src/types/music.ts` | 共用实体引用、音乐实体与专辑详情类型。 |
| `soul-music-room/src/components/layout/TopBar.tsx` | 实际提交搜索，保留 URL 关键词与分类，增加可点击搜索按钮。 |
| `soul-music-room/src/components/layout/layout.css` | 搜索输入与按钮布局、焦点样式。 |
| `soul-music-room/src/App.tsx` | 将 `/search` 切换到实际搜索页面。 |
| `soul-music-room/src/pages/StaticDestinationPage.tsx` | 删除已经实现的搜索占位定义。 |
| `soul-music-room/scripts/test-music-search.mjs` | API、状态管理、渲染、队列和异常路径回归检查。 |
| `soul-music-room/package.json` | 注册 `test:search`。 |

功能提交：`cbbafe5b649509e7dc07b0128e55d0384bd8abfe`。

## 自动化验证

以下命令在 `soul-music-room` 执行并通过：

```text
npm run test:search
npm run test:ai-layout
npm run test:ai-stream
npm run test:music-assets
npm run test:ai-trigger
npm run test:worldline
npm run lint
npm run build
```

搜索新增检查覆盖四类真实 DTO 映射、关键词编码、空查询、不合法参数、返回空结果、部分分类失败、全部失败、重新搜索、取消请求、旧响应晚到、无效实体、网络与 JSON 错误、HTTP 401、专辑 ID 不匹配、非法专辑歌曲及正文转义。渲染测试使用实际搜索 action 完成后的状态，没有删除或弱化失败检查。

初次执行时本地前端依赖缺失，使用现有 lockfile 执行 `npm ci` 恢复，未修改依赖版本或 lockfile。最终静态检查无错误和警告；构建仅保留已有的大于 500 kB 的 Vite chunk 提示。

## 浏览器验收

本地页面通过开发代理访问现有公网 Spring Boot，使用真实网易云数据，保持 TLS 校验。

- 原截图中的 Kanye 查询返回四类共 32 条结果，每类 8 条。
- `My Beautiful Dark Twisted Fantasy` 展开 14 首歌曲；视图聚焦到新展开的专辑标题，避免结果位于当前滚动区域之外。
- 点击加入队列后全局播放器显示 1 首；未登录播放显示正确登录提示。
- 周杰伦中文搜索正常；切换歌手分类返回 50 条，点击周杰伦结果跳转到歌曲分类。
- 刷新保留 `q=周杰伦&type=song`，顶部输入框与分类选择一致。
- 桌面 1280×720 和移动端 390×844 检查。移动端页面宽度和 scrollWidth 都为 390，无横向溢出，输入、搜索按钮和分类均可用。

## 上线

通过现有 `deploy/update.sh` 发布，仅重建前端，无数据库迁移。

- 网站：<https://47.83.162.191/search?q=Kanye>。
- 服务端 HEAD：`cbbafe5b649509e7dc07b0128e55d0384bd8abfe`，工作区干净。
- 完成时间：2026-09-30 22:21:19（Asia/Shanghai）。
- 部署记录：`/secure/moodflow/deployments/20260930T141936Z-cbbafe5b.json`，`status=passed`。
- 备份记录：`/secure/moodflow/backups/manifests/20260930T141938Z-cbbafe5b.json`，`status=complete`、`restore_verified=true`。
- Java、AI、前端、NetEase、PostgreSQL、Qdrant 全部健康，迁移退出码为 0。
- 发布脚本公网检查：首页、认证状态、健康接口、SSE、Worldline 均通过；Worldline 返回 9 个节点。
- 部署 SSH 连接被远端关闭。重新连接后确认部署记录已成功完成，并核对服务器 HEAD、工作区和公网页面；没有因连接中断重复发布。
- 公网加载已发布搜索页，Kanye 查询显示歌曲、歌手、专辑、歌单各 8 条，共 32 条；加入队列后播放器显示 1 首。
- 公网歌手分类显示 50 条结果，手机页面宽度和 scrollWidth 均为 390；歌单结果跳转到真实 ID `2913612358`，未登录时正确显示登录要求，返回搜索页保留关键词。
- 结束后恢复浏览器视口、关闭临时标签页并停止本地 5175 开发预览服务。

截图保存于 `search-page-assets-20260930/desktop.jpg` 和 `search-page-assets-20260930/mobile.jpg`。

## 限制

- 沿用服务端分页能力：全部分类每类显示前 8 条，单个分类最多 50 条；目前后端没有 offset/总数接口，本次没有伪造完整结果数量或增加未经支持的分页。
- 歌手入口执行以歌手名为关键词的歌曲检索，结果遵循网易云相关性排序，不声明为该歌手完整作品列表。
- 搜索无需登录，歌曲实际播放和歌单详情仍沿用原有账号认证要求；本次未使用真实登录账号验收音频播放。
- 原有未提交评测文件和数据未纳入提交，MainGraph、Agent Loop 与后端接口契约保持现状。
