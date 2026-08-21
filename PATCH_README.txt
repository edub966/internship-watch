Internship Pipeline v2 patch

This patch:
- adds Eightfold PCS-X support (used by Qualcomm's current careers site)
- switches Qualcomm from its stale Workday source to Eightfold
- makes internship status a hard gate AND requires a CE/SWE/hardware fit signal
- raises the default relevance threshold from 8 to 12

Apply this zip from the ROOT of your existing internship_pipeline folder with:

    unzip -o /path/to/internship_pipeline_patch_v2.zip

Then seed again before normal alerts:

    python -m src.main --seed
    python -m src.main

Your .env and data/jobs.db are NOT included in the patch and will not be overwritten.
