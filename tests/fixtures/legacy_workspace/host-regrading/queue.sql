BEGIN TRANSACTION;
CREATE TABLE jobs (id TEXT PRIMARY KEY, output TEXT NOT NULL, batch INTEGER NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, error TEXT);
INSERT INTO "jobs" VALUES('44444444444444444444444444444444','__WORKSPACE__/artifacts/partial-grade',1,'failed',1792972800.0,'fixture: interrupted scoring');
CREATE UNIQUE INDEX active_batch ON jobs(output,batch) WHERE status IN ('queued','running');
COMMIT;
