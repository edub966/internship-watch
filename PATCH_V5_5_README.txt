V5.5 roster hardening

Changes:
- Apple: adds the official Students: Internships team/subteam filter (STDNT/INTRN)
  in addition to the USA location scope, so the adapter no longer scans 1,000+
  unrelated US jobs just to find internships.
- Workday: for a posting that is already CE/target-year relevant but has an
  ambiguous list-view location, fetches only that posting's public CXS detail
  record and uses its full location/additionalLocations before the US gate.
  This is designed to fix boards such as Cadence/NXP that return vague location
  summaries without fetching details for every job on the board.
- Roster check now warns when a source scan exceeds 500 rows. This flags noisy
  boards such as Micron for optimization before scheduled production use.
- 52 tests pass in the assembled v5.5 tree; no live Tavily Search calls are used
  by the tests.

Required commissioning flow after install:
  python -m pytest -q
  python -m src.main --roster-check

Do NOT seed until all sources are healthy and large-scan warnings are reviewed.
