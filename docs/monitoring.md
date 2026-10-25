# Monitoring and completeness

Archive Admins (System Managers if there are none) get a bell notification when:

- an archive attempt fails, in the nightly run or through "Archive now": one notification per run listing the documents, each document at most once a day,
- the nightly run fails, or leaves eligible documents behind that it neither archived nor recorded as failed (e.g. because another job held the document),
- the nightly run has not finished for 26 hours (checked daily at 07:30, e.g. when the scheduler or worker is down),
- documents are ready but not archived for longer than **Overdue After (Hours)** (Archive Settings, default 48),
- archived documents have fewer copies than there are enabled storage targets,
- the audit log does not match the storage (see [Audit log](audit-log.md#audit-log)).

Each condition sends at most one notification per user and day (failed documents: one per new document and day), so a broken target does not flood the bell. While archiving is disabled, only mail originals are checked (see [Correspondence](correspondence.md#correspondence)).

The script report **Archive Completeness** ("Archiv-Vollständigkeit", Archive Admin and System Manager) lists overdue, failed and missing-copy documents with the time since when and the details (first error line, missing targets), and mail originals that failed or are pending for more than 24 hours (problem **Mail Original**, not shown when filtering by type or own company). With **Show Number Gaps** it also lists missing numbers per type, own company and number prefix (e.g. `RE-2026-002 - RE-2026-003` between `RE-2026-001` and `RE-2026-004`), for types that require a number or for the selected type. A gap is a hint, not an error: numbers can be skipped on purpose, and incoming documents carry the counterparty's numbers. The number card **Archive Problems** shows the count and opens the report; `docarchive.monitoring.problem_count()` gives hosts the same number.
