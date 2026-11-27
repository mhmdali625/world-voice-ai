# Visual memory database

The multimedia database of World Voice AI. It stores the useful frames from the
live stream, together with their text (OCR), objects, hazards, time and a
meaning vector. It can be searched by time, by keyword and by meaning.

Implementation: [memory_service.py](memory_service.py). Benchmark:
[bench_memory_db.py](bench_memory_db.py) (results in `bench_memory_db.json`).

## Storage layout

```
backend/storage/memory/            (WV_MEMORY_DIR overrides it)
  memory.db, memory.db-wal, -shm   SQLite database (WAL journal)
  frames/<frame id>.jpg            the kept frame, max 1024 px, JPEG quality 80 (~75 KB)
```

**Privacy:** every face in a frame (people, and faces in portraits, posters
or screens) is pixelated and blurred **before** the JPEG is written
([privacy.py](privacy.py), OpenCV YuNet detector, run locally). Only the face
regions change, so signs, text and objects keep their exact pixels. If faces
cannot be checked, the frame is not stored at all. At start, frames stored
before this protection existed (`faces IS NULL`) are anonymised in place, or
deleted if that's impossible, and image files without a row are removed.
Photo Mode stores only fully blurred photos (`storage/blurred`, Gaussian
radius 30), also deleted after 30 minutes.

The database stores the metadata and the path to each image. The image itself is a
file. A row and its file are always created and deleted together: retention,
the per-phone cap and Clear Memory all delete both.

## Schema

```sql
-- One row per USEFUL frame (sharp, new, informative) kept from a phone's stream.
CREATE TABLE frames (
    id            TEXT PRIMARY KEY,          -- random hex id, also the JPEG file name
    session       TEXT,                      -- WebRTC session (changes when the phone reconnects)
    device        TEXT NOT NULL DEFAULT 'phone',  -- stable phone id: memory isolation
    captured_at   REAL NOT NULL,             -- Unix time the frame was seen
    path          TEXT NOT NULL,             -- JPEG file
    width INTEGER, height INTEGER,
    sharpness REAL, brightness REAL, edges REAL, novelty INTEGER,  -- selection metrics
    enhanced      TEXT,                      -- pre-processing applied: 'brightened', 'sharpened'
    keep_reason   TEXT,                      -- 'new view' | 'periodic refresh'
    faces         INTEGER,                   -- faces anonymised before storage (NULL = never checked)
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | analyzed | failed
    caption       TEXT,                      -- one-sentence description (never identifies people)
    ocr_text      TEXT,                      -- all readable text, as written (Arabic / Latin)
    objects       TEXT,                      -- JSON list of object names
    hazards       TEXT,                      -- JSON list ('closed door ahead', ...)
    text_languages TEXT,                     -- JSON list of ISO codes found in the text
    embedding     BLOB,                      -- 768 float32: gemini-embedding-001 of caption+text+objects
    analyzed_at   REAL
);
CREATE INDEX frames_time        ON frames(captured_at);          -- retention, global timeline
CREATE INDEX frames_device_time ON frames(device, captured_at);  -- per-phone timeline and memory

-- Keyword search (SQLite FTS5) over what was read and seen.
CREATE VIRTUAL TABLE frames_fts USING fts5(id UNINDEXED, caption, ocr_text, objects);

-- Questions and answers (dashboard history; evidence used by each answer).
CREATE TABLE qa (
    id              TEXT PRIMARY KEY,
    device          TEXT NOT NULL DEFAULT 'phone',
    asked_at        REAL NOT NULL,
    question        TEXT,                    -- question text (Gemini's transcript for spoken questions)
    question_heard  TEXT,                    -- verbatim transcript of the audio, all languages
    answer          TEXT,
    answer_language TEXT,                    -- en | fr | ar | ar-LB
    frames_used     TEXT,                    -- JSON: frames given to the model + 'evidence' frames it used
    evidence        TEXT,                    -- strong | weak | none
    latency_s       REAL,
    mode            TEXT,                    -- 'live'
    first_word_s    REAL                     -- reported by the phone: user stopped speaking -> first audio
);
CREATE INDEX qa_time        ON qa(asked_at);
CREATE INDEX qa_device_time ON qa(device, asked_at);
```

Older databases without the `device` or `faces` columns are migrated
automatically when the server starts (`MemoryStore._migrate`).

## How each requirement maps to the database

| Requirement | How |
|---|---|
| Store frames with text, objects and time | `frames` row + JPEG; `ocr_text`, `objects`, `caption`, `captured_at` |
| Find by time | `frames_device_time` index: newest first, any time window (`/memory/frames?minutes=`) |
| Find by meaning | cosine similarity between the question's embedding and `embedding` (multilingual: a French question finds an English sign) |
| Find by keyword | FTS5 `frames_fts` |
| Past questions and answers | `qa` |
| Delete old frames | retention every 60 s: rows older than 30 min, plus the oldest beyond 600 frames per phone, are deleted with their files and FTS entries |
| Several phones | every read can be limited to one `device`; each phone has its own frame selector on the server |

## Write path and concurrency

- One SQLite connection. Every operation runs on a worker thread behind one
  async lock, so the event loop (video, questions) never blocks on disk.
- **WAL journal + `synchronous=NORMAL`**: a committed insert costs 1.0 ms
  (median) instead of 10.5 ms with the default journal. Readers never wait
  for the writer. This mode is crash-safe; a power cut can lose at most the
  last commits.
- Analysis results are written in one transaction per frame: row update,
  FTS replace, embedding.
- Transient analysis failures (Gemini 5xx, an incomplete reply) leave the
  frame `pending`. It is analysed again, and marked `failed` only after 3 attempts.

## Benchmark (bench_memory_db.py, this laptop, 60 s real time)

Load: 4 phones at once, plus background analysis (4 frames every 8 s), 2
dashboards polling every 3 s, and one question per phone every 6 s.
Latency includes waiting for the lock.

| Operation | realistic (a frame every 1.5 s per phone) p50 / p95 | stress (every sample kept, 2/s per phone) p50 / p95 |
|---|---|---|
| insert frame | 1.8 / 4.0 ms | 1.8 / 5.3 ms |
| save analysis (row + FTS + embedding) | 1.5 / 2.9 ms | 1.0 / 1.8 ms |
| question: memory index (40 newest) | 1.5 / 3.5 ms | 1.7 / 4.1 ms |
| question: load embeddings + rank | 1.5 / 3.3 ms | 1.7 / 3.0 ms |
| question: keyword (FTS5) | 1.0 / 1.9 ms | 1.1 / 2.4 ms |
| dashboard timeline / Q&A / stats | 2.7 / 1.9 / 1.3 ms | 2.7 / 1.7 / 1.3 ms |
| retention pass | 1.2 ms | 1.4 ms |

| Resource | realistic | stress |
|---|---|---|
| CPU (database work) | 2 % of one core | 4.6 % of one core |
| RAM (benchmark process) | 70 MB | 70 MB |
| Storage after 60 s | 4.2 MB db + 11.7 MB images | 4.2 MB db + 34.2 MB images |
| Storage at the 30-minute steady state, 4 phones (capped) | ~185 MB | ~185 MB |

Meaning search as memory grows (load + rank, pure Python with `math.sumprod`):

| Frames searched | Time |
|---|---|
| 150 | 22 ms |
| 600 (per-phone cap) | 72–102 ms |
| 2,400 (4 phones, all searched) | 271 ms |
| 10,000 | 1.25 s |

Integrity (after the stress run): `PRAGMA integrity_check` = ok; FTS rows =
analysed frames; every row has its file and every file has its row. Session
isolation is verified for every read type, for one phone's Clear Memory, and
end to end with 3 phones streaming at once (`test_multi_device.py`).

For comparison, the whole server with 3 phones streaming (video decoding,
frame selection, analysis) used 51 % of one core and 173 MB of RAM. The
database is not the bottleneck: Gemini (2–8 s per answer) and video
decoding are.

## Why SQLite and not PostgreSQL + pgvector

| Question | Measured | Verdict |
|---|---|---|
| Does the database slow an answer? | every database step of a question < 5 ms p95 under stress; Gemini takes 2,000–8,000 ms | no |
| Concurrent writers (4 phones)? | 8 commits/s at p95 5 ms; one writer is enough | no limit reached |
| Vector search scale? | 600 frames per phone is 70–100 ms; the 30-minute retention bounds the size | fine at this scale |
| Where would pgvector win? | > ~10,000 vectors (1.25 s here), or many servers sharing one database | not this project's workload |

PostgreSQL would add a server to install and run, with no measurable gain at
this size. **SQLite stays.** It should be reconsidered only if retention grows
from minutes to days, or if several backend servers must share one memory.
