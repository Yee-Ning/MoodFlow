from app.agent.memory.runtime import memory_backend
from app.agent.postgres_migrations import run_all_migrations
from app.config.settings import load_runtime_environment


def main() -> None:
    load_runtime_environment()
    include_memory = memory_backend() == "postgres"
    applied = run_all_migrations(include_memory=include_memory)
    if applied:
        print("Applied business migrations: " + ", ".join(applied))
    else:
        print("Business migrations are current")
    print("LangGraph checkpoint migrations are current")
    if not include_memory:
        print("Memory migration skipped because MEMORY_BACKEND is not postgres")


if __name__ == "__main__":
    main()
