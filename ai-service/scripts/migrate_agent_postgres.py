from app.agent.postgres_migrations import run_all_migrations


def main() -> None:
    applied = run_all_migrations()
    if applied:
        print("Applied business migrations: " + ", ".join(applied))
    else:
        print("Business migrations are current")
    print("LangGraph checkpoint migrations are current")


if __name__ == "__main__":
    main()
