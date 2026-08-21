v5.4 — company roster + zero-credit commissioning

Active roster (13):
NVIDIA, Qualcomm, Intel, Broadcom, Analog Devices, Cadence, Marvell, Micron,
NXP, Silicon Labs, Microsoft, Amazon, Apple.

New source adapters:
- Amazon: public Amazon Jobs JSON search, hard scoped to country=USA.
- Apple: public jobs.apple.com search session, hard scoped to Apple's USA facet.

New command:
  python -m src.main --roster-check

This fetches every enabled career source and reports fetched / relevant /
US-eligible counts. It makes NO DB writes, sends NO Discord alerts, and sends NO
Tavily Search requests. A failure exits non-zero and leaves your durable state
untouched.

Check one company only:
  python -m src.main --roster-check --company Intel

Do NOT seed the expanded roster until all enabled sources pass roster-check.
If one fails, send its FAIL line back so that adapter/config can be corrected
without spending Tavily credits or touching the job baseline.

Staged intentionally (not enabled yet): AMD, Arm, Google, Meta, Synopsys,
Texas Instruments, Tesla, SpaceX. Their current career systems need a dedicated
validated adapter before we trust them in the production watcher.
