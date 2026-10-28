# Experiment manifests

New runs write manifest schema 2. `manifest.json` contains the experiment,
versions, artifact digests and one task digest plus inventory references per
task. Detailed path-to-SHA256 maps live under `provenance/inventories/` and are
addressed by the SHA256 of `json.dumps(files, sort_keys=True)` (ASCII JSON).
Identical runtime inventories share an attachment. They are audit evidence,
not required for listing or comparing runs. Task SHA256 retains the original
full-file-map definition, so compacting a legacy manifest never changes scores
or task identities.

## Existing experiments

Run `agent-harness-eval manifest-index artifacts/<run>` once. This preserves the
original manifest byte-for-byte and writes `manifest.index.json` plus inventory
attachments. Reports and SWE export manifests continue to refer to the original
manifest SHA256. Console/report/export readers prefer a valid index; they fall
back to the original if its size, device/inode, mtime or ctime changes. The index
is a derived acceleration cache, not independent proof of authenticity.

`agent-harness-eval manifest-verify artifacts/<run>` explicitly checks the
original legacy manifest hash, inventory references and reconstructed task
digests. This expensive audit is separate from normal page reads. Keep the
original manifest and all attachments when archiving an experiment.

## New prepared SWE tasks

`materialize-grader.py` bundles the installed Python runtime and grading
packages into a tar archive under `.cache/grader-bundles/`. It hashes and stores
the detailed inventory at build time. Subsequent preparations of the unchanged
installed runtime reuse this archive by source file identity, including ctime;
same package names/versions alone never authorize reuse. Cache rebuilds are
needed after installed source files change. Task preparation hard-links the
archive where possible, falling back to copying across filesystems. Docker
`ADD grader-runtime.tar` extracts it at the original runtime path.

At run freeze, hashes are reused only for the same unchanged inode within that
single freeze. A shared archive is hashed once rather than scanning thousands
of dependencies once per task. Frozen run artifacts are independent copies.
Unpacked legacy tasks retain full hashing; they are never silently treated as
equivalent based on a version string. Task-specific scripts/configuration and
the archive remain part of the task digest. The pinned image digest is recorded
in the frozen runtime provenance and Dockerfile; image contents are not scanned.

## Agent source snapshots