Internship Pipeline v5.2 patch

Fixes:
- Tavily /usage parser now handles API keys whose key-level limit is null/unlimited by falling back safely to account.plan_usage/account.plan_limit.
- --preflight now executes the same zero-Search-credit Tavily usage guard used in production and prints the usage source + remaining credits.
- Exact-post queries no longer duplicate the company name when the ATS title already starts with it.
- Recruiter query explicitly prioritizes United States university/early-career recruiters.
- Added regression tests for null key limits, account-plan fallback, unusable usage responses, and query cleanup.

Install from project root:
  unzip -o ~/Downloads/internship_pipeline_patch_v5_2_root.zip
  source .venv/bin/activate
  python -m pytest -q
  python -m src.main --preflight

Expected tests: 41 passed.
The preflight makes GET /usage but does not make a Tavily Search request.
