"""P3 durable snapshots and progress outbox, isolated from existing ledgers."""

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


class EventsStore:
    def __init__(self, database_url):
        self.pool = ConnectionPool(database_url, min_size=1, max_size=8,
                                   kwargs={"row_factory": dict_row})
        try:
            with self.pool.connection() as db:
                row = db.execute("SELECT to_regclass('public.agent_progress_stream') AS name").fetchone()
                if row["name"] is None:
                    raise RuntimeError("P3 schema missing; run scripts.migrate_agent_postgres")
        except BaseException:
            self.pool.close()
            raise

    def close(self):
        self.pool.close()

    def playcount(self, song_id):
        with self.pool.connection() as db:
            row = db.execute("SELECT play_count FROM agent_song_playcount WHERE song_id=%s", (song_id,)).fetchone()
            return row["play_count"] if row else 0

    def increment_playcount(self, song_id):
        with self.pool.connection() as db:
            return db.execute("""INSERT INTO agent_song_playcount(song_id,play_count) VALUES(%s,1)
                ON CONFLICT(song_id) DO UPDATE SET play_count=agent_song_playcount.play_count+1,
                updated_at=NOW() RETURNING play_count""", (song_id,)).fetchone()["play_count"]

    def flush_playcount(self, song_id, epoch, delta):
        if delta < 0:
            raise ValueError("negative playcount delta")
        with self.pool.connection() as db, db.transaction():
            db.execute("INSERT INTO agent_song_playcount(song_id) VALUES(%s) ON CONFLICT DO NOTHING", (song_id,))
            db.execute("SELECT play_count FROM agent_song_playcount WHERE song_id=%s FOR UPDATE", (song_id,))
            row = db.execute("SELECT flushed_delta FROM agent_playcount_flush WHERE song_id=%s AND epoch=%s", (song_id, epoch)).fetchone()
            previous = row["flushed_delta"] if row else 0
            upto = max(previous, delta)
            total = db.execute("UPDATE agent_song_playcount SET play_count=play_count+%s, updated_at=NOW() WHERE song_id=%s RETURNING play_count",
                               (upto - previous, song_id)).fetchone()["play_count"]
            db.execute("""INSERT INTO agent_playcount_flush VALUES(%s,%s,%s)
                ON CONFLICT(song_id,epoch) DO UPDATE SET flushed_delta=EXCLUDED.flushed_delta""", (song_id, epoch, upto))
            return total, upto

    def append_progress(self, run_id, scope, conversation, mode, payload):
        with self.pool.connection() as db, db.transaction():
            db.execute("""INSERT INTO agent_progress_stream(run_id,auth_scope,conversation_id,graph_mode)
                VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (run_id, scope, conversation, mode))
            owner = db.execute("SELECT * FROM agent_progress_stream WHERE run_id=%s FOR UPDATE", (run_id,)).fetchone()
            if (owner["auth_scope"], owner["conversation_id"], owner["graph_mode"]) != (scope, conversation, mode):
                raise PermissionError("progress run scope mismatch")
            seq = db.execute("UPDATE agent_progress_stream SET next_seq=next_seq+1 WHERE run_id=%s RETURNING next_seq", (run_id,)).fetchone()["next_seq"]
            db.execute("INSERT INTO agent_progress_event VALUES(%s,%s,%s,DEFAULT)", (run_id, seq, Jsonb(payload)))
            return f"{seq}-0", payload

    def require_scope(self, run_id, scope):
        with self.pool.connection() as db:
            row = db.execute("SELECT auth_scope FROM agent_progress_stream WHERE run_id=%s", (run_id,)).fetchone()
            if row is None or row["auth_scope"] != scope:
                raise KeyError("progress run unavailable")

    def latest_seq(self, run_id):
        with self.pool.connection() as db:
            row = db.execute("SELECT next_seq FROM agent_progress_stream WHERE run_id=%s", (run_id,)).fetchone()
            return row["next_seq"] if row else 0

    def progress(self, run_id, since=0, limit=100):
        with self.pool.connection() as db:
            rows = db.execute("""SELECT event_seq,payload FROM agent_progress_event
                WHERE run_id=%s AND event_seq>%s ORDER BY event_seq LIMIT %s""", (run_id, since, limit)).fetchall()
            return [(f'{row["event_seq"]}-0', row["payload"]) for row in rows]

    def deliver(self, run_id, group, consumer, entries, claim_idle=None):
        accepted = []
        with self.pool.connection() as db, db.transaction():
            for event_id, payload in entries:
                seq = int(event_id.split("-", 1)[0])
                db.execute("""INSERT INTO agent_progress_delivery(run_id,group_name,event_seq,consumer_name)
                    VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (run_id, group, seq, consumer))
                row = db.execute("""SELECT *, EXTRACT(EPOCH FROM (NOW()-delivered_at)) AS idle
                    FROM agent_progress_delivery WHERE run_id=%s AND group_name=%s AND event_seq=%s FOR UPDATE""", (run_id, group, seq)).fetchone()
                if not row["acknowledged"] and (row["consumer_name"] == consumer or claim_idle is not None and row["idle"] >= claim_idle):
                    db.execute("""UPDATE agent_progress_delivery SET consumer_name=%s,delivered_at=NOW()
                        WHERE run_id=%s AND group_name=%s AND event_seq=%s""", (consumer, run_id, group, seq))
                    accepted.append((event_id, payload))
        return accepted

    def consume(self, run_id, group, consumer, limit=100, claim_idle=None):
        with self.pool.connection() as db:
            rows = db.execute("""SELECT e.event_seq,e.payload FROM agent_progress_event e
                LEFT JOIN agent_progress_delivery d ON e.run_id=d.run_id AND e.event_seq=d.event_seq AND d.group_name=%s
                WHERE e.run_id=%s AND (d.event_seq IS NULL OR (NOT d.acknowledged AND
                (d.consumer_name=%s OR (%s::double precision IS NOT NULL AND EXTRACT(EPOCH FROM (NOW()-d.delivered_at))>=%s))))
                ORDER BY e.event_seq LIMIT %s""", (group, run_id, consumer, claim_idle, claim_idle, limit)).fetchall()
        return self.deliver(run_id, group, consumer, [(f'{r["event_seq"]}-0', r["payload"]) for r in rows], claim_idle)

    def ack(self, run_id, group, consumer, event_id):
        with self.pool.connection() as db:
            return db.execute("""UPDATE agent_progress_delivery SET acknowledged=TRUE
                WHERE run_id=%s AND group_name=%s AND consumer_name=%s AND event_seq=%s AND NOT acknowledged
                RETURNING event_seq""", (run_id, group, consumer, int(event_id.split("-", 1)[0]))).fetchone() is not None

    def acknowledged(self, run_id, group, event_id):
        with self.pool.connection() as db:
            row = db.execute("""SELECT acknowledged FROM agent_progress_delivery
                WHERE run_id=%s AND group_name=%s AND event_seq=%s""",
                (run_id, group, int(event_id.split("-", 1)[0]))).fetchone()
            return bool(row and row["acknowledged"])
