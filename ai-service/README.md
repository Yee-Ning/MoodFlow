# MoodFlow AI Service

A minimal FastAPI service that accepts one user message and returns a strictly
schema-constrained LLM answer. It uses DeepSeek V4 Flash through the Responses API
with JSON Schema output, followed by local Pydantic validation.

## Setup

```powershell
cd D:\code\ProjectsAll\MoodFlow\ai-service
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and replace `LLM_API_KEY`. The demo expects
`LLM_BASE_URL=https://api.deepseek.com` and `LLM_MODEL=deepseek-v4-flash`.

## Run

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

## Try it

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8001/api/ai/chat `
  -ContentType 'application/json' `
  -Body '{"message":"Recommend a song for a quiet evening."}'
```

The old message-only body remains valid. To let the agent resolve references
such as “这首歌”, send lightweight application-state references:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8001/api/ai/chat `
  -ContentType 'application/json' `
  -Body '{"message":"推荐几首和这首类似的歌","context":{"current_song_id":185809,"current_playlist_id":null,"page":"/library"}}'
```

The AI service resolves `current_song_id` through Spring Boot
`GET /api/songs/{id}` and injects the resulting song metadata as trusted runtime
context. It does not call the NetEase API directly and does not load lyrics.
Spring Boot timeouts, errors, invalid payloads, and missing songs degrade to an
empty current-song context instead of failing the chat request.

## Test

The tests mock the model call and do not consume API quota.

```powershell
python -m unittest discover -s tests -v
```

## Try the music search tool

Start the local NetEase API on port `3000` and the MoodFlow Spring Boot backend
on port `8080`, then run:

```powershell
python -c "import asyncio; from app.tools.music import search_music; print(asyncio.run(search_music('周杰伦')).model_dump_json(indent=2))"
```

The tool calls the Spring Boot endpoint `GET /api/music/search`; it never calls
the NetEase API directly.

Runtime context and tools share the same agent loop: the first model step sees
the resolved system context and can then call `search_music` normally when it
needs catalog data for a recommendation.

## Tool Calling Agent Loop

`search_music` is exposed to DeepSeek as a function definition generated from
`SearchMusicArgs.model_json_schema()`. The agent validates returned arguments,
executes the registered Python function, and sends a JSON `function_call_output`
with the same `call_id` back to DeepSeek. DeepSeek is stateless, so the service
resends the user input, every response output item, and every tool result on each
step. The loop is capped at five steps.

Start the NetEase and Spring Boot services, then send a concrete lookup request
and watch the agent/tool logs:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8001/api/ai/chat `
  -ContentType 'application/json' `
  -Body '{"message":"请帮我搜索周杰伦的歌曲，返回 5 首"}'
```
