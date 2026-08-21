V5.1 production-candidate patch: credit safety + US-only alert/enrichment gate

This is the rebuilt V5. Install this instead of the earlier V5 archive.
It includes every V5 safety improvement plus the location changes discovered
before first intentional Tavily testing.

Key changes:
- Tavily searches use documented include_domains instead of Google-style site: syntax.
- Basic depth is explicit; auto_parameters is disabled.
- 10-credit hard per-run cap and 250-credit protected reserve by default.
- Tavily /usage is checked before search; search fails closed if usage cannot be verified.
- Company-level recruiter + UF searches are cached for 14 days.
- Job-specific post search is cached for 18 hours.
- Alert queue prevents a Discord/Tavily failure from permanently losing a new internship.
- Networking has its own durable queue and survives per-run credit caps.
- SQLite WAL removed so GitHub Actions persists the complete database in one jobs.db file.
- GitHub Actions state is persisted even after failures and concurrent runs are serialized.
- --preflight validates configuration and shows planned searches without Tavily Search calls.
- NVIDIA and Qualcomm are configured for the 2027 internship cycle.
- US-only gate is applied BEFORE both Discord alerting and Tavily enrichment.
- International jobs are still stored for dedupe/history but cannot consume Tavily credits.
- Ambiguous locations fail closed: stored, but no Discord alert and no Tavily request.
- Queued jobs are revalidated immediately before Tavily and Discord as a second safety gate.
- Eightfold location parsing treats its trailing standardized token as COUNTRY code, avoiding
  false positives such as Israel (IL) being mistaken for Illinois or Canada (CA) for California.
- Workday supports common US "City, ST" locations while rejecting common Canadian province codes.
- Eligibility state is durable: --seed establishes a no-alert baseline; if an existing posting's
  location is later corrected from ambiguous/non-US to confidently US, it can alert exactly once.
- 37 zero-credit automated tests pass in the assembled project.
