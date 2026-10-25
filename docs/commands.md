# Commands

```bash
bench --site <site> docarchive-run --dry-run   # list what would be archived, write nothing
bench --site <site> docarchive-run             # archive now (needs Enabled), exit 1 on failures
bench --site <site> docarchive-smoke --target primary
bench --site <site> docarchive-sync-mode --target primary   # after switching objects to COMPLIANCE
bench --site <site> docarchive-extract --dry-run            # e-invoice data for existing documents
bench --site <site> docarchive-demo                         # demo data on an empty developer site
```

`docarchive-demo` only runs on a site with `developer_mode` and no Business Documents. It creates an own company, four counterparties (Contacts with the documents panel), invoices with a number gap, a contract, a bank statement that fails its checksum, two documents with missing fields, an overdue invoice and an e-mail thread with a gap. It then runs the nightly archive, sets a legal hold, extends a retention and builds an export. Storage is an in-memory Object Lock bucket for the duration of the command, so it needs no S3 credentials; the database keeps the results, the objects are gone afterwards. Because the storage targets lead nowhere afterwards, the command turns off the site's scheduler. `scripts/screenshots.py` takes the screenshots above from such a site.

`docarchive-smoke` works without Enabled. It uploads a tiny `_smoke/` object with a 1-day GOVERNANCE lock, reads it back and checks its retention. Then it tries to delete that version with the target's credentials, once normally and once with `BypassGovernanceRetention`. Both must be refused with AccessDenied (or the provider's object-lock error), and a versioned HEAD must still find the object. It exits with status 1 if any check fails. Each run leaves one small object that expires after a day.
