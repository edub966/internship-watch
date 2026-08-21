v5.7 deployment hardening

- GitHub Actions cadence changed from every 30 minutes to hourly at minute 17.
- Scheduled automatic enrichment is explicitly recruiter-only.
- Scheduled search max is 1 query per networking job.
- Tavily budget is capped at 4 credits per run and 8 credits per rolling 24h for this bot.
- First cloud run automatically seeds when no persisted jobs.db exists, preventing an alert storm.
- README deployment instructions updated for hourly cadence.
