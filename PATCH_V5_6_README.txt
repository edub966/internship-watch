Internship Pipeline v5.6 roster hardening

Changes:
- Fix Apple Students: Internships API filter encoding:
  teamsAndSubTeams-STDNT / subTeam-INTRN.
- Apple search now requests newest ordering for deterministic monitoring.
- Switch Micron from the huge Workday crawl to Micron's current Eightfold careers board.
- Make Workday location diagnostics honest: a successful detail request is no longer
  described as a successful location classification unless it actually resolves US/non-US.
- --roster-check now prints up to two sample ambiguous relevant jobs to make remaining
  location problems diagnosable without Tavily, Discord, or DB writes.
- 54 tests pass in the assembled v5.6 tree.

Install over v5.5:
  unzip -o ~/Downloads/internship_pipeline_patch_v5_6_root.zip
  source .venv/bin/activate
  python -m pytest -q
  python -m src.main --roster-check

Do not --seed until the roster check is reviewed.
