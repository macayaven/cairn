# A compaction beside the replica service: what was measured

Evidence for the open question of decision 143, not a decision. Measured on 2026-11-17 on a laptop (Docker 29.4.0, linux/arm64), with the shipped stack: `deploy/compose.yaml` and `deploy/compose.backup.yaml` as they ship, plus the stack rehearsal's override that gives Litestream a directory instead of a bucket (`scripts/rehearse_stack.py`, `OVERRIDE`). Caddy was not started. cairn was built from `main` at `8dc856a` (image `sha256:e370316979466bfdb2e8cf3b6cd7d3f67dfa0fc318df3391d928e6e1f1b8923e`, SQLite 3.46.1); Litestream is the pinned `litestream/litestream:0.5.18@sha256:513bff62a88fc0c86c73d27333c41c54c89fe890a7c32a551600adff9309b11b`.

How. A second process in the cairn container (`docker compose exec -T cairn python -`) wrote 150 notes into a workspace and 150 into the default workspace, then deleted that workspace with `Store.delete_account`. The deleted workspace's project name and notes held a made-up word, and its occurrences in the raw bytes of `cairn.db` and `cairn.db-wal` were counted. Each checkpoint try opened a new connection with Python's default busy timeout of 5000 ms, the timeout cairn's own store uses, ran `PRAGMA wal_checkpoint(MODE)`, recorded the row (busy, frames in the log, frames checkpointed) and slept one second. Each series of 60 tries came after a fresh `VACUUM`. The database was 1.6 to 3.5 MB; the log file had grown to 13.6 to 18.1 MB while the notes were written. Four runs: run 1 with the shipped config, the deleted notes written first, and 45 minutes of series and readings; runs 2 and 3 with the deleted notes written after the others, run 2 with `truncate-page-n: 500` added to the config and run 3 without; run 4 with that setting, Litestream at `-log-level debug`, and one series while notes were written.

## What happens today

- The deletion's own truncating checkpoint waited about 5.2 s and gave up in every run, and the store logged that the old pages stay in the log.
- `serve`'s compaction ran at its first poll, 618 s after the start (run 1), and logged `cairn.access: compact not finished: another connection held the file in 5243 ms`. That line is the one the compaction alert counts.
- From a second connection, with Litestream running and nothing else writing (run 1):

  | Mode | Busy | Frames in the log | Checkpointed by the try | Each try took |
  |---|---|---|---|---|
  | TRUNCATE | 60 of 60 | 839 for the first 9 tries, then 1 | 0 | 5149 to 5223 ms |
  | RESTART | 60 of 60 | 839 at the first try, then 1 | 0 | 5163 to 5217 ms |
  | PASSIVE | 0 of 60 | 839 at the first try, then 1 | 0 | 0 to 4 ms |

  No try truncated or reset the log, and none copied a frame. Within 1 to about 56 seconds of each `VACUUM` the log went from 839 frames to 1. In run 1 that is inferred to be Litestream's own checkpoint, since it logs none at its default level; in run 4 its debug log shows it: a PASSIVE checkpoint of all 732 frames 2 seconds after the deletion, and then one about every minute. The gap in which Litestream holds no read transaction is that checkpoint (`db.go` at v0.5.18: `execCheckpoint` calls `releaseReadLock` first, and `acquireReadLock` exists "to prevent checkpointing"), so on an idle database there is about one gap per series, and most tries have nothing to hit.
- While a note was being written every second (run 4), TRUNCATE was busy in 60 of 60 tries, and Litestream's debug log shows five of its own checkpoints in those six minutes. Each try waited about 5 s, and while it waited it kept writers out (SQLite's `wal_checkpoint_v2` page: TRUNCATE and RESTART "block new database writer attempts while" pending): the writer meant to write a note a second wrote 16 in 90 seconds. That also fits run 1, where the log took 9 TRUNCATE tries to go down and 1 under PASSIVE.
- Control, Litestream stopped (`docker compose stop litestream`), a `VACUUM` before each series (run 1): TRUNCATE was never busy, and the first try took the log file from 14,016,272 bytes to 0. RESTART and PASSIVE were never busy and checkpointed every frame (863 of 863, 872 of 872); the file kept its size (3,555,592 and 3,592,672 bytes).
- The log file for 10 minutes after a `VACUUM`, with Litestream running, read every 30 seconds (run 1): 14,016,272 bytes at every reading while idle, and the same at every reading for 10 more minutes with a note written every 30 seconds (20 notes; the database grew from 3,432,448 to 3,534,848 bytes). It never shrank in any run. After Litestream's checkpoint new writes start again at the front of the file, so old frames are overwritten front first, and only as far as new writes reach.
- The deleted text. Right after the deletion the database file held the word 604 times; about 40 seconds later, after Litestream's checkpoint, 0 (runs 2 and 3). In the log file: run 1, with the deleted notes written first, read 227 after the deletion and 0 at the next reading, after `serve`'s compaction 10 minutes later. Runs 2 and 3, with them written after 150 other notes, read 512 then 496 (run 2) and 604 (run 3) for about 5 minutes after the deletion, through a `VACUUM` and 3 idle minutes after it, until the file grew from about 13.6 to 16,257,552 bytes during a TRUNCATE series, by writes the measurement did not make; then 0. Run 4 read 604 for 2 minutes after the deletion and 204 at the end, 8 minutes after it.

## Litestream's settings that bear on truncation

- `strings` on `/usr/local/bin/litestream` from the pinned image lists four keys: `checkpoint-interval`, `min-checkpoint-page-count`, `truncate-page-n` and `busy-timeout`. `litestream replicate -h` lists no flag for checkpoints. `deploy/litestream.yml` sets none of the four: its `dbs:` entry (line 15) sets `sync-interval: 10s` (line 22) and nothing else.
- From Litestream's source at tag v0.5.18, read through a fetch tool that quoted fragments (not read line by line, and the end of `db.go` past 100,000 characters not at all): in `cmd/litestream/main.go` the four keys are fields of `DBConfig`, so they go on a `dbs:` entry. In `db.go`: `DefaultCheckpointInterval = 1 * time.Minute`, `DefaultMinCheckpointPageN = 1000`, `DefaultTruncatePageN = 121359` ("~500MB with 4KB page size"), `DefaultBusyTimeout = 1 * time.Second`; `checkpointIfNeeded` runs a TRUNCATE checkpoint only when the log is over `TruncatePageN` pages ("Emergency truncate checkpoint"), with a PASSIVE one beside it, and otherwise PASSIVE checkpoints on the page count or the interval; the comment on `DB` says RESTART was removed (issue 724); `effectiveTruncatePageN` reads 0 as the default, so the threshold can be moved, not turned off.
- Run 4 shows the setting read and what it does here. With `truncate-page-n: 500` (mounted over `/etc/litestream.yml`, seen in the container's mounts), Litestream logged, 2 seconds after the deletion: `wal restarted by passive checkpoint, skipping truncate checkpoint` with `wal_size=3015872 threshold=2060032`. The threshold is the setting's (500 pages); the truncation was skipped because the PASSIVE checkpoint had already let the log start again. The log file kept its 18,148,632 bytes to the end, as in run 2. So, as measured, this setting does not shrink the file after a compaction.

## What each of decision 143's options would take

1. **Ask for the truncating checkpoint while Litestream is between reads.** 240 tries of TRUNCATE and RESTART (runs 1 to 4), idle and under writes, got through 0 times. The only gap is Litestream's own checkpoint, about once a minute, and a try that waits for it keeps writers out, Litestream's checkpoint included, for up to its busy timeout. A retry loop would compete with the checkpoint it waits for; this would take a way to ask Litestream itself to truncate, which 0.5.18 does not offer beyond the threshold above.
2. **Stop the replica service for the seconds a compaction takes.** With Litestream stopped the truncating checkpoint finished at once and emptied the log file. `serve` cannot stop another container, so it would take the host: on the VM `host.sh` stopping `litestream.service`, compacting, and starting it again; with Compose, `docker compose stop litestream` around `compact`. While it is stopped nothing is copied; a Litestream that starts copies what changed while it was down (seen for decision 132). The Compose sequence was run three times, below; `host.sh compact` has not run on a VM.
3. **Say the limit in the privacy guide.** What it would say, from these numbers: beside the replica service the deleted text leaves the database file within about a minute of the deletion, but the log file keeps its largest size and keeps old pages until later writes reach them (5 to more than 8 minutes here; no bound was found), and the replica keeps older copies until its retention ends (`deploy/litestream.yml`, line 45: 192 hours).

## The sequence, run

Run on 2026-11-17, same laptop and stack as above, cairn built from branch `ops/a-compaction-with-the-replica-paused` (image `sha256:df5f0164467e463839ed85868498f512f2bbff09e30dd2ab142aa14a33a3dce3`; nothing under `src/` changed from `feat/batch-2026-11-17`). Each run used a new Compose project and volumes: 150 notes in the default workspace, 150 in a second workspace whose project name and notes held the made-up word, then `Store.delete_account` on the second, then the four commands of [`deploy/README.md`, "After a workspace is deleted"](../deploy/README.md#after-a-workspace-is-deleted), read from the guide and run as written.

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| Deletion's own checkpoint | gave up after 5.2 s | gave up after 5.2 s | gave up after 5.2 s |
| Before: database / log file | 2,891,776 / 9,859,192 B | 2,883,584 / 17,761,352 B | 2,883,584 / 9,471,912 B |
| Before: the word in database / log file | 604 / 544 | 604 / 604 | 604 / 540 |
| `stop`, `compact`, `start` (s) | 0.20, 1.15, 1.91 | 0.28, 1.47, 1.95 | 0.26, 1.46, 1.92 |
| Replica service stopped, by the commands / by Litestream's log | 3.26 / 3.32 s | 3.70 / 3.72 s | 3.64 / 3.68 s |
| `compact` printed | 2891776 -> 1609728 bytes | 2883584 -> 1609728 bytes | 2883584 -> 1609728 bytes |
| Right after `compact`: log file; the word in database / log file | 0 B; 0 / 0 | 0 B; 0 / 0 | 0 B; 0 / 0 |
| Right after `compact`: the compaction record | only `store.compacted_at` | same | same |
| Newest copy after `start` (start began) | 04:45:24Z (04:45:21Z) | 04:48:34Z (04:48:31Z) | 04:51:23Z (04:51:20Z) |
| Replica folder: before, end of sequence, 60 s later (B) | 810,201; 1,104,576; 1,212,614 | 589,356; 825,429; 933,469 | 556,698; 793,060; 901,103 |
| Litestream errors after the start | 0 | 0 | 0 |

After the sequence the log file held 4,152 bytes (one page written after the start) and the word stayed at 0 in both files through the next minute. The fourth command's newest copy, a level-0 file of 108,038 to 108,043 bytes, was stamped 3 seconds after `start` began in each run, so a copy stamped after the start is how the guide and `host.sh compact` tell the replica is current again.

A write while the replica service is stopped. In each run `litestream` was stopped again, one note was written through `Store` in a second process in the cairn container, and `litestream` was started: the newest level-0 copy was stamped 3 s after the start (04:46:29Z after 04:46:26Z; 04:49:39Z after 04:49:36Z; 04:52:28Z after 04:52:25Z), and the restore below holds that note.

A restore from the replica. Each run then stopped the stack, removed the data volume, and started it again, so the shipped `litestream restore -if-replica-exists` built `cairn.db` from the replica folder. In all three: `PRAGMA integrity_check` returned `ok`; 151 notes (the default workspace's 150 and the note written while the service was stopped); the only account `default` and the only project `keeper`; the word 0 times in the restored database and log file.

Bytes uploaded. The replica is a folder, so its size stands in for what a bucket would receive: from just before the stop to the end of the sequence it grew by 294,375, 236,073 and 236,362 bytes, and by about 108 KB more in the next minute, against a database of 1.6 MB after `compact`. Litestream's files are compressed, so the word's count in them proves nothing and was not taken; a restore of the latest state does not hold it, and older restore points in the replica still do until its retention (192 hours) ends.

## What was not measured

- Google Cloud: the VM, the bucket, Container-Optimized OS, and the pre-release database and its sizes.
- Bytes sent to a bucket (the folder's growth above stands in for it); a restore to a point in time before the compaction, which still holds the word.
- Index keys as such: the word was in a project name and note text; a page's slug was not used.
- What wrote the 2.7 MB that grew the log file during the TRUNCATE series of runs 2 and 3; cairn's own log (run 4) showed only its start and one stats line.
- Anything past 10 minutes after a `VACUUM`, and a database larger than a few MB.

The scripts that drove these runs were not added to the repository.
