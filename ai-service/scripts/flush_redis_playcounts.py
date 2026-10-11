"""One P3 flush; normal service lifespan also schedules periodic snapshots."""

from app.redis_events.runtime import playcounts


def main():
    counter = playcounts()
    if counter is None:
        raise RuntimeError("PLAYCOUNT_BACKEND=redis is required")
    try:
        counter.flush_all()
    finally:
        if counter.client is not None:
            counter.client.close()
        counter.store.close()


if __name__ == "__main__":
    main()
