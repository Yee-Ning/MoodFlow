# MoodFlow AI Service

FastAPI + LangGraph 音乐 Agent。当前聊天路由会先通过 Spring Boot 解析正在播放的歌曲上下文，再让绑定真实业务工具的 LangChain ChatModel 自主决定直接回答或调用工具。

## Setup

```powershell
cd D:\code\ProjectsAll\MoodFlow\ai-service
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env` 并设置 `LLM_API_KEY`。默认示例使用 OpenAI-compatible Chat Completions 接口。

## Run

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

## Agent Graph

```text
START
  -> load_song_context
  -> agent
       -> tools -> agent  (最后一条 AIMessage 有 tool_calls)
       -> END             (没有 tool_calls)
```

`MusicAgentState.messages` 继续使用 LangGraph 的 `add_messages` reducer，`InMemorySaver` 按 `thread_id` 保留 `HumanMessage`、`AIMessage(tool_calls)`、`ToolMessage` 和最终 `AIMessage`。每次 HTTP 请求只调用一次 `graph.astream`，并保持 `stream_mode=["messages", "updates"]`。

工具调用最多执行 5 次。超过上限时，`agent` 返回友好终止消息。`ToolNode` 把校验、后端或网络异常转换为模型可理解的错误 `ToolMessage`，React 只接收节点名称与完成状态，不接收工具内部结果。

## Tools

- `search_music(keyword, limit=10)`：`GET /api/music/search`
- `get_song_lyrics(song_id)`：`GET /api/songs/{song_id}/lyrics`
- `get_user_playlists()`：`GET /api/user/playlists`
- `add_song_to_playlist(playlist_id, song_id)`：`POST /api/playlists/{playlist_id}/songs/{song_id}`

所有工具均只通过 Python `MusicServiceClient` 调用 Spring Boot，不直接访问网易云或数据库。搜索结果使用 `song_id/name/artists/album`，歌单结果使用 `playlist_id/name/editable`，便于后续工具继续使用真实 ID。

需要登录的工具不接受 `user_id` 或 Cookie 参数。FastAPI 只从当前请求读取 `JSESSIONID`，通过 LangGraph `context_schema` 和 `ToolRuntime` 注入工具；Spring Boot 继续负责登录、歌单归属、参数和业务异常校验。

## Streaming API

```powershell
curl.exe -N -X POST http://127.0.0.1:8001/api/ai/chat `
  -H "Content-Type: application/json" `
  -H "Accept: text/event-stream" `
  -d '{"message":"搜索 Open Arms","thread_id":"demo-thread","current_song_id":null}'
```

SSE 事件为 `message`、`update`、`final`、`error` 和 `done`。工具链请求的 `update` 通常依次出现 `load_song_context`、`agent`、`tools`、`agent`。

## Test

测试使用脚本化模型和 MockTransport，不消耗模型额度，也不会执行真实写操作。

```powershell
python -m unittest discover -s tests -v
```
