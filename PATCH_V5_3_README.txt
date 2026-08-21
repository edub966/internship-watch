V5.3 credit-efficiency hardening

Changes:
- Automatic Tavily enrichment now defaults to recruiter-only (1 reusable company search, cached 14 days).
- Exact-post search is opt-in via: python -m src.main --deep-enrich COMPANY REQ
- UF-engineer search is opt-in via --include-uf after live validation returned 0 raw results.
- Adds a local rolling-24h Tavily credit ledger/cap (default 20) so a lagging /usage endpoint cannot cause a runaway scheduled spend.
- Every paid search attempt is conservatively logged before the request.
- Recruiter results with duplicate public-post content are collapsed so Discord shows more diverse contacts.
- Preflight labels AUTO vs MANUAL searches and reports local rolling-24h spend.

No real Tavily Search request is made by tests or --preflight.
