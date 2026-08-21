Internship Pipeline v3 patch
============================

What changed
------------
1. Eightfold (Qualcomm) now requests sort_by=timestamp instead of relevance.
   This makes pagination suitable for monitoring instead of a relevance-ranked
   result set that can reshuffle between runs.
2. The database now stores EVERY fetched posting, not only CE-relevant ones.
   Relevance controls alerts; it no longer controls whether a requisition is
   remembered as already existing.
3. Successful company scans mark postings that vanished from the current scan
   as inactive, and the CLI reports a disappeared count.
4. Eightfold refuses to sync an incomplete scan when the API-reported total is
   larger than the number of unique jobs collected.
5. Output is now per-company, e.g.:
   Qualcomm: 43 fetched, 12 relevant, 0 new relevant, 0 disappeared

IMPORTANT: run --seed once after installing this patch. v2 stored only relevant
jobs, while v3 stores the full fetched internship set.
