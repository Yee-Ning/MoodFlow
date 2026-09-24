"""Synthetic conversation corpus and temporary real-pgvector replay for Jev v2."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.memory.models import MemoryRecord, MemoryRetrievalRequest, MemoryType
from app.agent.memory.postgres_store import _vector_text
from app.agent.memory.retriever import MemoryRetriever
from evals.jev_shadow_v1 import DATASETS, DockerPsqlReadOnlyStore, write_jsonl

MEMORIES = DATASETS / "synthetic_memories_v2.jsonl"
DATASET = DATASETS / "memory_eval_v2.jsonl"
USERS = [f"jev-v2-synthetic-user-{index}" for index in range(3)]

PREFERENCES = [
    "周杰伦", "陈奕迅", "林俊杰", "五月天", "孙燕姿", "王菲", "轻音乐", "爵士乐",
    "民谣", "电子音乐", "古典钢琴", "独立摇滚", "City Pop", "R&B", "Lo-fi",
    "氛围音乐", "电影配乐", "管弦乐", "雷鬼", "布鲁斯", "拉丁流行", "合成器流行",
    "新世纪音乐", "中文说唱", "Bossa Nova", "后摇", "放克", "原声吉他", "交响乐", "电子舞曲",
]
NEGATIVES = [
    "重金属", "过度压缩的录音", "现场版", "刺耳高音", "广告插播", "低音过重",
    "过快节奏", "自动播放相似歌曲", "剧透式歌曲解说", "重复副歌", "音量突变",
    "失真吉他", "过长前奏", "悲伤情歌", "纯器乐长曲",
]
HABITS = [
    ("睡前", "舒缓钢琴"), ("晨跑", "节奏明快的流行乐"), ("通勤", "轻快民谣"),
    ("写代码", "低干扰氛围音乐"), ("做饭", "节奏轻松的爵士乐"),
    ("健身", "鼓点清晰的电子乐"), ("阅读", "无歌词轻音乐"),
    ("下雨天", "安静的原声吉他"), ("周末打扫", "积极的放克"),
    ("冥想", "缓慢环境音乐"), ("学习", "低音量古典钢琴"),
    ("夜间散步", "柔和的 City Pop"), ("开车", "稳定节拍的摇滚"),
    ("午休", "自然声与轻音乐"), ("聚会", "容易合唱的流行歌"),
    ("画画", "电影配乐"), ("加班", "不吵闹的 Lo-fi"),
    ("旅行", "轻快的独立流行"), ("瑜伽", "舒展的氛围音乐"),
    ("整理照片", "怀旧中文流行乐"),
]
EPISODES = [
    ("和弦", "上次用户表示想学习常见和弦进行"),
    ("歌词", "上次用户想逐句理解一首歌的歌词意象"),
    ("鼓点", "上次用户问过如何听出歌曲的鼓点"),
    ("转调", "上次用户想知道歌曲为什么会转调"),
    ("混音", "上次用户问过现场版与录音室版混音差异"),
    ("歌单", "上次用户整理过一份周末公路旅行歌单"),
    ("钢琴", "上次用户尝试用钢琴弹奏一段副歌"),
    ("节拍", "上次用户询问三拍子和四拍子的听感"),
    ("演唱会", "上次用户计划看一场小型独立音乐演出"),
    ("音色", "上次用户比较过木吉他与电吉他的音色"),
    ("唱片", "上次用户想了解黑胶唱片的播放方式"),
    ("采样", "上次用户问过电子音乐中的采样概念"),
    ("编曲", "上次用户想知道一首歌曲如何完成编曲"),
    ("乐队", "上次用户询问乐队中贝斯的作用"),
    ("母带", "上次用户想比较母带处理前后的音量"),
]
CONFLICT_TOPICS = ["摇滚", "现场录音", "慢歌", "英文歌", "电子舞曲"]
UNRELATED = [
    ("天气", "上次用户询问过周末天气"), ("火车", "上次用户查询过火车时刻"),
    ("菜谱", "上次用户询问过番茄炒蛋菜谱"), ("电脑", "上次用户询问过电脑更新"),
    ("牙医", "上次用户预约过牙医"), ("快递", "上次用户查询过快递进度"),
    ("电影", "上次用户询问过电影放映时间"), ("账单", "上次用户查询过电费账单"),
    ("公交", "上次用户询问过公交线路"), ("植物", "上次用户询问过室内植物养护"),
]


def synthetic_memories() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = "2026-09-20T12:00:00+00:00"
    older = "2024-01-15T12:00:00+00:00"

    def add(category: str, topic: str, content: str, memory_type: str,
            *, user_index: int | None = None, temporal_status: str = "current",
            scene: str | None = None) -> None:
        number = sum(row["metadata"]["category"] == category for row in rows) + 1
        index = len(rows) if user_index is None else user_index
        row = {
            "memory_id": f"jev-v2-{category}-{number:03d}", "user_id": USERS[index % 3],
            "memory_type": memory_type, "content": content,
            "metadata": {"category": category, "topic": topic, "temporal_status": temporal_status,
                         "scene": scene, "synthetic": True},
            "memory_key": f"jev-v2:{category}:{number:03d}",
            "source_thread_id": f"jev-v2-replay-{index % 3}",
            "confidence": .9, "importance": .7, "evidence_strength": "explicit",
            "confirmations": 1, "created_at": older if temporal_status == "former" else now,
            "updated_at": older if temporal_status == "former" else now,
            "status": "active", "version": 1, "superseded_by": None,
        }
        rows.append(row)

    for topic in PREFERENCES:
        add("music_preference", topic, f"用户喜欢{topic}的音乐", "profile")
    for topic in NEGATIVES:
        add("negative_preference", topic, f"用户不喜欢{topic}", "profile")
    for scene, style in HABITS:
        add("habit", scene, f"用户在{scene}时通常听{style}", "profile", scene=scene)
    for topic, content in EPISODES:
        add("episodic", topic, content, "episodic")
    for index, topic in enumerate(CONFLICT_TOPICS):
        add("conflict", topic, f"2024年用户曾喜欢{topic}", "episodic",
            user_index=index, temporal_status="former")
        add("conflict", topic, f"用户现在不喜欢{topic}", "profile", user_index=index)
    for topic, content in UNRELATED:
        add("unrelated", topic, content, "episodic")
    assert len(rows) == 100
    for row in rows:
        record = {key: value for key, value in row.items() if key != "metadata"}
        record["structured_content"] = row["metadata"]
        record["embedding"] = None
        MemoryRecord.model_validate(record)
    return rows


def _query_for(row: dict[str, Any]) -> tuple[str, str, str | None]:
    category = row["metadata"]["category"]
    topic = row["metadata"]["topic"]
    if category == "music_preference":
        return f"我对{topic}的音乐是什么态度？", "preference_query", None
    if category == "negative_preference":
        return f"推荐歌曲时，我对{topic}有什么限制？", "recommendation", None
    if category == "habit":
        return f"我在{topic}时通常想听什么音乐？", "mood_scene_recommendation", topic
    if category == "episodic":
        return f"我上次关于{topic}聊了什么？", "song_analysis", None
    if category == "conflict":
        when = "过去" if row["metadata"]["temporal_status"] == "former" else "现在"
        return f"我{when}对{topic}是什么态度？", "preference_query", None
    return f"我上次关于{topic}问过什么？", "unrelated_question", None


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _psql_script(sql: str) -> None:
    process = subprocess.run(
        ["docker", "exec", "-i", "moodflow-postgres", "psql", "-U", "root",
         "-d", "moodflow_agent", "-X", "-q", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False,
    )
    if process.returncode:
        raise RuntimeError("temporary synthetic pgvector import or cleanup failed")


def _delete_sql() -> str:
    users = ",".join(_sql_literal(user) for user in USERS)
    return ("DELETE FROM agent_long_term_memory WHERE "
            f"user_id IN ({users}) AND memory_id LIKE 'jev-v2-%';")


def _insert_sql(rows: list[dict[str, Any]], embeddings: dict[str, list[float]]) -> str:
    columns = (
        "memory_id,user_id,memory_type,memory_key,content,structured_content,"
        "source_thread_id,confidence,importance,evidence_strength,confirmations,"
        "created_at,updated_at,status,version,superseded_by,embedding"
    )
    values = []
    for row in rows:
        embedding = embeddings.get(row["memory_id"])
        fields = [
            _sql_literal(row["memory_id"]), _sql_literal(row["user_id"]),
            _sql_literal(row["memory_type"]), _sql_literal(row["memory_key"]),
            _sql_literal(row["content"]),
            _sql_literal(json.dumps(row["metadata"], ensure_ascii=False)) + "::jsonb",
            _sql_literal(row["source_thread_id"]), str(row["confidence"]),
            str(row["importance"]), _sql_literal(row["evidence_strength"]),
            str(row["confirmations"]), _sql_literal(row["created_at"]),
            _sql_literal(row["updated_at"]), _sql_literal(row["status"]),
            str(row["version"]), "NULL",
            (_sql_literal(_vector_text(embedding)) + "::vector") if embedding else "NULL",
        ]
        values.append("(" + ",".join(fields) + ")")
    return f"BEGIN; {_delete_sql()} INSERT INTO agent_long_term_memory ({columns}) VALUES " + ",".join(values) + "; COMMIT;"


def prepare_memory_v2() -> Path:
    """Insert only synthetic users, retrieve through pgvector, then remove them."""
    from app.agent.memory.embeddings import QwenMemoryEmbedder

    rows = synthetic_memories()
    write_jsonl(MEMORIES, rows)
    embedder = QwenMemoryEmbedder()
    episodic = [row for row in rows if row["memory_type"] == "episodic"]
    vectors = embedder.embed_documents([row["content"] for row in episodic])
    if len(vectors) != len(episodic) or any(len(vector) != embedder.dimension for vector in vectors):
        raise ValueError("synthetic episode embedding mismatch")
    embeddings = {row["memory_id"]: vector for row, vector in zip(episodic, vectors, strict=True)}
    store = DockerPsqlReadOnlyStore()
    output: list[dict[str, Any]] = []
    _psql_script(_insert_sql(rows, embeddings))
    try:
        retriever = MemoryRetriever(store, embedder=embedder)
        for row in rows:
            query, task, scene = _query_for(row)
            modes = [("default", None)]
            if row["memory_type"] == "episodic":
                modes.append(("episodic_only_probe", MemoryType.EPISODIC))
            for mode, memory_type in modes:
                request = MemoryRetrievalRequest(user_id=row["user_id"], query=query,
                                                 scene=scene, memory_type=memory_type, top_k=8)
                start = datetime.now(UTC)
                result = retriever.retrieve(request)
                latency_ms = (datetime.now(UTC) - start).total_seconds() * 1000
                candidate_rows = [
                    {"memory_id": item.memory_id, "memory_type": item.memory_type.value,
                     "content": item.content, "relevance": item.relevance,
                     "gold_useful": item.memory_id == row["memory_id"],
                     "retrieved": item.model_dump(mode="json")}
                    for item in result.items
                ]
                output.append({"case_id": f"memory-v2-{mode}-{len(output) + 1:03d}",
                               "user_id": row["user_id"], "query": query,
                               "recent_context": [
                                   {"role": "user", "content": row["content"]},
                                   {"role": "assistant", "content": "我会记住这条信息。"},
                               ],
                               "task": task, "scene": scene, "retrieval_mode": mode,
                               "gold_memory_ids": [row["memory_id"]],
                               "candidate_memories": candidate_rows,
                               "baseline_retrieval_latency_ms": round(latency_ms, 2),
                               "label_rule": "target fact required; other candidate facts irrelevant for this narrow query"})
                write_jsonl(DATASET, output)
    finally:
        _psql_script(_delete_sql())
    return DATASET
