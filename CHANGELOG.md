# Changelog

## [0.8.1](https://github.com/EffizienzAgent/docarchive/compare/v0.8.0...v0.8.1) (2026-10-25)


### Bug Fixes

* **correspondence:** stateless and multi-pod deployments are supported: mail originals are written straight to the file storage (S3 via s3storage) instead of a local spool folder; while the storage is down, mails up to 10 MB are kept in the database and file writes pause for 5 minutes; spooled v0.8.0 originals are moved by a migration patch ([#41](https://github.com/EffizienzAgent/docarchive/pull/41)) ([4653788](https://github.com/EffizienzAgent/docarchive/commit/46537888efcc1d704ee29f2923f4e22944a57161), [bcd3f4d](https://github.com/EffizienzAgent/docarchive/commit/bcd3f4de6561e7dd2161367c4d60592ba828ca4a), [9287f55](https://github.com/EffizienzAgent/docarchive/commit/9287f5596c24f1aa1e41205089d90dc39ac2d484))
* files stored by s3storage are read natively, without a host `docarchive_read_file` hook ([#41](https://github.com/EffizienzAgent/docarchive/pull/41)) ([e464f57](https://github.com/EffizienzAgent/docarchive/commit/e464f5752c719c94090e6afbe5e4552c5396b1bb), [a3e0c7f](https://github.com/EffizienzAgent/docarchive/commit/a3e0c7f537963dd8bad3082a1498b1a36bdd7da1))


### Documentation

* short README with the details in docs/, German screenshots, documents panel on its own tab in the demo ([#39](https://github.com/EffizienzAgent/docarchive/pull/39)) ([94c72de](https://github.com/EffizienzAgent/docarchive/commit/94c72dea744beea1c6ceb0b9f3a234dabfdc7442))

## [0.8.0](https://github.com/EffizienzAgent/docarchive/compare/v0.7.0...v0.8.0) (2026-10-25)


### Features

* **audit log:** one entry per archive event (archive runs, legal holds, retention extensions, deletions, exports, settings), each written as its own Object Lock object; nightly reconciliation of database and buckets with a bell notification; audit entries per document in the desk and in the export viewer ([#33](https://github.com/EffizienzAgent/docarchive/pull/33)) ([e3e95f1](https://github.com/EffizienzAgent/docarchive/commit/e3e95f15c5acd64924cd9e851ba9bb4c5082d4cd))
* **monitoring:** bell notifications for failed archive attempts, failed or incomplete nightly runs, missed runs, overdue documents and missing copies; "Archive Completeness" report with optional number gaps and an "Archive Problems" number card ([#35](https://github.com/EffizienzAgent/docarchive/pull/35)) ([c673a6e](https://github.com/EffizienzAgent/docarchive/commit/c673a6ebea8b16bb7e5c0847cc29c10235bf1812))
* **correspondence:** mail originals are stored in a background job instead of inside the mail receive, send and mailsync jobs; failed originals are retried, never aged out, and shown in Archive Completeness ([#38](https://github.com/EffizienzAgent/docarchive/pull/38)) ([e6d87fe](https://github.com/EffizienzAgent/docarchive/commit/e6d87fe4fcb55f8733da8b83f0db854a5d011d6e))
* `bench docarchive-demo` seeds demo data; screenshots in the README ([#37](https://github.com/EffizienzAgent/docarchive/pull/37)) ([adb8ebf](https://github.com/EffizienzAgent/docarchive/commit/adb8ebf39791bcf69571f92c658205b3249dd379))


### Bug Fixes

* **correspondence:** the nightly run archives correspondence documents again; the documents panel no longer reports channel and direction as missing ([#38](https://github.com/EffizienzAgent/docarchive/pull/38)) ([f89b3fd](https://github.com/EffizienzAgent/docarchive/commit/f89b3fd1737edac70a8c3cf071813576906e34ac))
* **correspondence:** sent mails are rebuilt only within an hour of sending, and the rebuild time is recorded ([#38](https://github.com/EffizienzAgent/docarchive/pull/38)) ([28476cb](https://github.com/EffizienzAgent/docarchive/commit/28476cb454ebae0b8c814888589496a2739b89d4))
* **correspondence:** deleting a Communication never fails because of its mail original ([#38](https://github.com/EffizienzAgent/docarchive/pull/38)) ([2078ec0](https://github.com/EffizienzAgent/docarchive/commit/2078ec0bffa82ac68339ed17ef4fedf8273b9255))
* **correspondence:** migrate only marks the app's own or unused Correspondence types ([#38](https://github.com/EffizienzAgent/docarchive/pull/38)) ([9d2b587](https://github.com/EffizienzAgent/docarchive/commit/9d2b587d8a45024fd7f9feb72a243467dc35f1e3))
* **correspondence:** thread gaps reveal Communications only to readers; the viewer caps inlined images ([#38](https://github.com/EffizienzAgent/docarchive/pull/38)) ([00ce4f6](https://github.com/EffizienzAgent/docarchive/commit/00ce4f6be4f9caa2a6da3fa573b9efb44c386725), [68b7b8c](https://github.com/EffizienzAgent/docarchive/commit/68b7b8c303ffb3fe1699599615fdb0c3b3711c51))


### Documentation

* feature overview at the top of the README ([#36](https://github.com/EffizienzAgent/docarchive/pull/36)) ([fb74ed9](https://github.com/EffizienzAgent/docarchive/commit/fb74ed9faf6640eca00b099fd41c51860ac1b46a))

## [0.7.0](https://github.com/EffizienzAgent/docarchive/compare/v0.6.0...v0.7.0) (2026-10-25)


### Features

* **correspondence:** archive e-mails as Business Documents of type Correspondence or Internal Correspondence (6 years), from the original `.eml` captured when Frappe receives or sends a mail, or from the IMAP Sent folder through mailsync's `mailsync_mail_read` hook ([#31](https://github.com/EffizienzAgent/docarchive/pull/31)) ([7075810](https://github.com/EffizienzAgent/docarchive/commit/70758105be705a167865bbcfbad1a29a317cbf9c))
* **correspondence:** per e-mail account mode (off, manual, automatic), internal mails, contacts as counterparty, keep period for unarchived originals and source of outgoing originals in Archive Settings ([#31](https://github.com/EffizienzAgent/docarchive/pull/31))
* **correspondence:** "Archive" on Communications, automatic counterparty from timeline links, discard before archiving, cleanup after the keep period ([#31](https://github.com/EffizienzAgent/docarchive/pull/31))
* **correspondence:** viewer for archived mails (sanitised, remote images blocked, attachments, checksum), thread view with gaps, closing gaps by uploading an `.eml` ([#31](https://github.com/EffizienzAgent/docarchive/pull/31))
* channel, direction and thread fields on Business Documents, so letters and faxes can be filed as correspondence too ([#31](https://github.com/EffizienzAgent/docarchive/pull/31))


### Changes

* `Business Document.source_name` is now a Data field instead of a Dynamic Link, so archived documents no longer block deleting their source record ([#31](https://github.com/EffizienzAgent/docarchive/pull/31))

## [0.6.0](https://github.com/EffizienzAgent/docarchive/compare/v0.5.0...v0.6.0) (2026-10-25)


### Features

* **export viewer:** keyboard navigation (English and German shortcut help), a fixed metadata header and branding ([#29](https://github.com/EffizienzAgent/docarchive/pull/29)) ([c215457](https://github.com/EffizienzAgent/docarchive/commit/c215457ee7344422b7bcd530eec95ad4364edabe), [31ea0f8](https://github.com/EffizienzAgent/docarchive/commit/31ea0f8ce6eea2118f58fbcd392449cb5dc0bad3))
* **documents panel:** the preview header stays fixed while scrolling ([#29](https://github.com/EffizienzAgent/docarchive/pull/29)) ([744cbe0](https://github.com/EffizienzAgent/docarchive/commit/744cbe052f0c556a801f4c1b1a8f7fe9f4f7c9b0))

## [0.5.0](https://github.com/EffizienzAgent/docarchive/compare/v0.4.1...v0.5.0) (2026-10-25)


### Features

* open or delete the current document from triage ([f6f83f4](https://github.com/EffizienzAgent/docarchive/commit/f6f83f41040905ca0767edb4ba73806c3edeba6f))
* row selection, bulk bar and side preview in the documents panel ([4c9c2b3](https://github.com/EffizienzAgent/docarchive/commit/4c9c2b39d66779020462358735f834f4b5a0258a))

## [0.4.1](https://github.com/EffizienzAgent/docarchive/compare/v0.4.0...v0.4.1) (2026-10-25)


### Bug Fixes

* harden the archive export: safe viewer data, recovery of interrupted exports, one export at a time, download-only active files, User Permissions and admin-only export downloads ([c1fa7bd](https://github.com/EffizienzAgent/docarchive/commit/c1fa7bdbfd95341ac1e0146091b55318a207d162))

## [0.4.0](https://github.com/EffizienzAgent/docarchive/compare/v0.3.0...v0.4.0) (2026-10-25)


### Features

* export the archive as ZIP packages with an offline viewer, mail previews and verified checksums; requires s3storage v0.3.0 ([4e279bd](https://github.com/EffizienzAgent/docarchive/commit/4e279bddf266c0bd4a92cf868eb4f69bd1801fae)), closes [#19](https://github.com/EffizienzAgent/docarchive/issues/19)

## [0.3.0](https://github.com/EffizienzAgent/docarchive/compare/v0.2.0...v0.3.0) (2026-10-25)


### Features

* **documents panel:** search, type/year/status filters, date sorting, row actions, bulk "Archive now" and a drop zone; the upload dialog has one row per file and a date in the file name prefills the document date ([#10](https://github.com/EffizienzAgent/docarchive/pull/10)) ([952f7ef](https://github.com/EffizienzAgent/docarchive/commit/952f7ef8d1db5b791fb6eba63edcdb631fad8c19))
* **triage dialog** for documents that need work: file preview and fields side by side, save and next, skip and back with keyboard shortcuts; "Assign" and "Assign all" in the list ([#12](https://github.com/EffizienzAgent/docarchive/pull/12)) ([50250be](https://github.com/EffizienzAgent/docarchive/commit/50250bee288c3c4f5c4ac8291df41a2e6f03911c))
* **manual retention extension** by years or to a date, one by one or in bulk with a preview, recorded in a locked sidecar; "Retention Overview" report and a "Retention ends in 90 days" number card ([#13](https://github.com/EffizienzAgent/docarchive/pull/13)) ([98d1bf4](https://github.com/EffizienzAgent/docarchive/commit/98d1bf45169fbea488bfc4b6b6eb7e83cdc8fdb7))
* **multiple files per document:** an Attachments table archived next to the main file with the same lock; uploads add new attachments to a known document ([#14](https://github.com/EffizienzAgent/docarchive/pull/14)) ([f99c99a](https://github.com/EffizienzAgent/docarchive/commit/f99c99a685ccc16fc984e14cc41a6c235934e6b0), [3f4003e](https://github.com/EffizienzAgent/docarchive/commit/3f4003ec8aff1e0dd183c4f491568a48ef86d7df))
* **legal hold and deletion:** legal hold per copy (also at archive time), release and deletion of expired documents with an admin key, "Expired Documents" report and a deletion log, attachments included ([#15](https://github.com/EffizienzAgent/docarchive/pull/15)) ([b98d1d3](https://github.com/EffizienzAgent/docarchive/commit/b98d1d39e9303911fe297cbdb2877af505edc242), [42dde9f](https://github.com/EffizienzAgent/docarchive/commit/42dde9f5136f011dd9224d37609de2b2fe340f32), [1c53423](https://github.com/EffizienzAgent/docarchive/commit/1c53423da681ca27b401e0acab3076c7393c45ee), [531d23c](https://github.com/EffizienzAgent/docarchive/commit/531d23cc4adbe64641304de37c0895d24cf1c6f7)), closes [#11](https://github.com/EffizienzAgent/docarchive/issues/11)
* **e-invoice metadata:** reads XRechnung/EN 16931 (CII, UBL) and ZUGFeRD/Factur-X and suggests date, number, title, type and own company without overwriting set values; the upload date is optional and `docarchive-extract [--dry-run]` backfills existing documents ([#16](https://github.com/EffizienzAgent/docarchive/pull/16)) ([36d63fc](https://github.com/EffizienzAgent/docarchive/commit/36d63fcc371256c4b9f2c56912e67b09753ec388), [29b2ece](https://github.com/EffizienzAgent/docarchive/commit/29b2ece7bfdcb96b7890cf563c99d923a84e30b4))
* **bucket policy scripts:** `scripts/apply_bucket_policy.py` sets a deny-only archive bucket policy, `scripts/check_object_lock.py` checks what the writer and admin keys can do ([#18](https://github.com/EffizienzAgent/docarchive/pull/18)) ([e3de81b](https://github.com/EffizienzAgent/docarchive/commit/e3de81baf12dc9edffe0aff72ff1799c1b746000))


### Bug Fixes

* **upload API:** content-based deduplication only matches documents from the same `source_system` and `source_account`, so an upload never returns or replaces another source's document ([#14](https://github.com/EffizienzAgent/docarchive/pull/14)) ([cf1c560](https://github.com/EffizienzAgent/docarchive/commit/cf1c560f1e83afe3426834180ccbc877399efe40))

## [0.2.0](https://github.com/EffizienzAgent/docarchive/compare/v0.1.1...v0.2.0) (2026-10-25)


### Features

* **upload API** for integrations: create Business Documents with their file, deduplicated per `source_system`/`source_account`/`source_key`; attached files are writable only with write access on the document ([#5](https://github.com/EffizienzAgent/docarchive/pull/5)) ([0c77a42](https://github.com/EffizienzAgent/docarchive/commit/0c77a42e0a7b3a72b80a07ac3223c44feaa0c2fa), [84fd387](https://github.com/EffizienzAgent/docarchive/commit/84fd387ddafc40bc242986b750b098d800eb7ebc))
* **nightly archive run** to Object Lock storage at 02:30, on demand with `bench docarchive-run [--dry-run]` or the **Archive now** form and list action; every copy is verified by version (lock mode, retain-until, SHA-256), failed targets are retried, adds `docarchive-smoke` ([#7](https://github.com/EffizienzAgent/docarchive/pull/7)) ([553c7fc](https://github.com/EffizienzAgent/docarchive/commit/553c7fc176c18feeb52ac18ac8db7cc6cda20cab), [c16e3dd](https://github.com/EffizienzAgent/docarchive/commit/c16e3dd431166d8a4f8cf55615235f6afdcbb1e8), [91f964e](https://github.com/EffizienzAgent/docarchive/commit/91f964e416d94acac734e59baf1d528ec780abb8))
* **retention extension and mode sync**: moving a contract end date later extends the retention of the file and its metadata on every target, never shortens it; `docarchive-sync-mode` records COMPLIANCE after the manual switch and only upgrades from GOVERNANCE ([#8](https://github.com/EffizienzAgent/docarchive/pull/8)) ([ddf4165](https://github.com/EffizienzAgent/docarchive/commit/ddf4165f7dd798beb00215d4d11eb3a44abc4113), [7d1823e](https://github.com/EffizienzAgent/docarchive/commit/7d1823e57ece344f5eb7ca64093d79501f561100))
* **source record fields**: Business Documents can point to the record they come from (`source_doctype`/`source_name`, locked after archive start, written to `.meta.json`) ([#7](https://github.com/EffizienzAgent/docarchive/pull/7)) ([db2eab5](https://github.com/EffizienzAgent/docarchive/commit/db2eab576078044abaa8bf47354d6e029e162b84))

## [0.1.1](https://github.com/EffizienzAgent/docarchive/compare/v0.1.0...v0.1.1) (2026-10-24)


### Bug Fixes

* register protect_object under the final s3storage object hooks ([f23a057](https://github.com/EffizienzAgent/docarchive/commit/f23a0579796be0b88865fc248fa24dc91b99be4f))

## 0.1.0 (2026-10-24)


### Features

* business documents with lock, storage targets and host hooks ([4c76a87](https://github.com/EffizienzAgent/docarchive/commit/4c76a87e49214feef4adf2ac3dd1dc685790ca67))
* contract end date may only move later after archive start ([9128e4c](https://github.com/EffizienzAgent/docarchive/commit/9128e4c69f2e378e1b8960b1380b30445c5d3637))
