Internship Pipeline v4 patch

What changed
------------
1. Eightfold pagination repair
   - Primary scan remains timestamp-sorted.
   - If Eightfold repeats a row across a page boundary, the scraper probes
     nearby offsets and then shifted page boundaries if necessary.
   - Results are unioned by Eightfold's internal position ID.
   - A partial snapshot still fails closed and is never synced.

2. Clearer scan output
   - A repaired scan prints e.g. "pagination repaired +1 job(s)".
   - Seed summary reports how many companies synced and how many failed safely.

Install from the internship_pipeline folder:
    unzip -o ~/Downloads/internship_pipeline_patch_v4_root.zip
    source .venv/bin/activate
    python -m pytest -q
    python -m src.main --seed
    python -m src.main
