# Encoder probe speed

Successful `ffprobe` results are now cached briefly in-process by absolute path,
modification time, and file size. Mux, merge, compress, encode, and upload paths
that probe the same unchanged file repeatedly reuse the cached summary instead of
starting another `ffprobe`. Cache entries are deep-copied on read and write so
callers cannot mutate shared state. Failed/empty probes are not cached. The cache
is bounded to 128 entries and expires after 300 seconds; it is not shared across
processes and does not survive restart.

Upload metadata now comes from one cached probe (`media_metadata`) instead of two
separate `ffprobe` calls for duration and dimensions. Merge probes run
concurrently with `probe_many`, and mux probes its video and track concurrently.
Screenshots are captured in one ffmpeg process with one input per timestamp;
missing frames fall back to the previous per-frame command, and the single
process remains attached to the job registry for pause/cancel. These changes do
not alter encoder settings, output quality, worker counts, or hardware
selection. They do not benchmark real FFmpeg timing and do not prove
multi-process or real MongoDB behavior.
