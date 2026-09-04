# MoodFlow AI Service

A FastAPI service whose current chat route runs a minimal LangGraph current-song
assistant. It resolves trusted song metadata and lyrics through Spring Boot, then
uses DeepSeek through the Responses API with JSON Schema output and local Pydantic
validation.

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
  -Body '{"message":"这首歌表达了什么？","thread_id":"demo-thread","current_song_id":185809}'
```

The old message-only and nested `context` bodies remain valid for compatibility.
New clients should send the current song reference at the top level:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8001/api/ai/chat `
  -ContentType 'application/json' `
  -Body '{"message":"那第二段呢？","thread_id":"demo-thread","current_song_id":185809}'
```

The graph is `START -> load_song_context -> song_assistant -> END`. Every invoke
explicitly overwrites `current_song_id` and `song_context`, while an in-memory
checkpointer retains message history by `thread_id`. The AI service resolves the
song through Spring Boot `GET /api/songs/{id}/context`; it never calls NetEase or
the music database directly. Lyrics are length-limited and treated as untrusted
analysis data in the model prompt.

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
the NetEase API directly. This direct command is only for isolated tool testing.

## Existing Tool Calling Agent Loop

`search_music` is exposed to DeepSeek as a function definition generated from
`SearchMusicArgs.model_json_schema()`. The agent validates returned arguments,
executes the registered Python function, and sends a JSON `function_call_output`
with the same `call_id` back to DeepSeek. DeepSeek is stateless, so the service
resends the user input, every response output item, and every tool result on each
step. The loop is capped at five steps.

This code is retained for a later module, but it is not connected to the current
song graph or `/api/ai/chat`. The current Song Assistant calls the same base model
without any tool definitions.
