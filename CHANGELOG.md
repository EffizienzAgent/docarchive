# Changelog

## [0.4.0](https://github.com/EffizienzAgent/docarchive/compare/v0.3.0...v0.4.0) (2026-10-25)


### Features

* business documents with lock, storage targets and host hooks ([4c76a87](https://github.com/EffizienzAgent/docarchive/commit/4c76a87e49214feef4adf2ac3dd1dc685790ca67))
* contract end date may only move later after archive start ([9128e4c](https://github.com/EffizienzAgent/docarchive/commit/9128e4c69f2e378e1b8960b1380b30445c5d3637))
* cover attachments in legal holds and deletion ([531d23c](https://github.com/EffizienzAgent/docarchive/commit/531d23cc4adbe64641304de37c0895d24cf1c6f7))
* e-invoice metadata extractor ([36d63fc](https://github.com/EffizienzAgent/docarchive/commit/36d63fcc371256c4b9f2c56912e67b09753ec388))
* extend contract retention and track the lock mode ([ddf4165](https://github.com/EffizienzAgent/docarchive/commit/ddf4165f7dd798beb00215d4d11eb3a44abc4113))
* filterable documents panel and per-file upload dates ([952f7ef](https://github.com/EffizienzAgent/docarchive/commit/952f7ef8d1db5b791fb6eba63edcdb631fad8c19))
* hold at archive time, admin key for later holds ([42dde9f](https://github.com/EffizienzAgent/docarchive/commit/42dde9f5136f011dd9224d37609de2b2fe340f32))
* legal hold and deletion of expired documents ([b98d1d3](https://github.com/EffizienzAgent/docarchive/commit/b98d1d39e9303911fe297cbdb2877af505edc242)), closes [#11](https://github.com/EffizienzAgent/docarchive/issues/11)
* manual retention extension and retention overview ([98d1bf4](https://github.com/EffizienzAgent/docarchive/commit/98d1bf45169fbea488bfc4b6b6eb7e83cdc8fdb7))
* merge attachments into a known upload ([3f4003e](https://github.com/EffizienzAgent/docarchive/commit/3f4003ec8aff1e0dd183c4f491568a48ef86d7df))
* multiple files per document ([f99c99a](https://github.com/EffizienzAgent/docarchive/commit/f99c99a685ccc16fc984e14cc41a6c235934e6b0))
* nightly archive run to Object Lock storage ([553c7fc](https://github.com/EffizienzAgent/docarchive/commit/553c7fc176c18feeb52ac18ac8db7cc6cda20cab))
* optional upload date and e-invoice backfill ([29b2ece](https://github.com/EffizienzAgent/docarchive/commit/29b2ece7bfdcb96b7890cf563c99d923a84e30b4))
* scripts to set and check archive bucket policies ([ad53e73](https://github.com/EffizienzAgent/docarchive/commit/ad53e7300b5a9be89e0f9ff46706827bb41d4708))
* scripts to set and check archive bucket policies ([e3de81b](https://github.com/EffizienzAgent/docarchive/commit/e3de81baf12dc9edffe0aff72ff1799c1b746000))
* show legal hold in panel and retention overview ([1c53423](https://github.com/EffizienzAgent/docarchive/commit/1c53423da681ca27b401e0acab3076c7393c45ee))
* source record fields and a single content function ([db2eab5](https://github.com/EffizienzAgent/docarchive/commit/db2eab576078044abaa8bf47354d6e029e162b84))
* triage dialog for documents that need work ([50250be](https://github.com/EffizienzAgent/docarchive/commit/50250bee288c3c4f5c4ac8291df41a2e6f03911c))
* upload API for integrations ([0c77a42](https://github.com/EffizienzAgent/docarchive/commit/0c77a42e0a7b3a72b80a07ac3223c44feaa0c2fa))


### Bug Fixes

* bound and harden manual retention extension ([5523ff7](https://github.com/EffizienzAgent/docarchive/commit/5523ff7930f6c19c20a3926043abc4f5b030f8f6))
* check pending extensions directly in the deleted test ([ecf00a9](https://github.com/EffizienzAgent/docarchive/commit/ecf00a9132321451109c8439d92d71ba63920a72))
* guard filter storage and hide deleted documents ([5b17e27](https://github.com/EffizienzAgent/docarchive/commit/5b17e27669eb5ac8e0ace7ab8e0cb25b67a24c42))
* harden retention extension and mode sync after security review ([7d1823e](https://github.com/EffizienzAgent/docarchive/commit/7d1823e57ece344f5eb7ca64093d79501f561100))
* harden the archive run after security review ([c16e3dd](https://github.com/EffizienzAgent/docarchive/commit/c16e3dd431166d8a4f8cf55615235f6afdcbb1e8))
* harden the upload API after security review ([84fd387](https://github.com/EffizienzAgent/docarchive/commit/84fd387ddafc40bc242986b750b098d800eb7ebc))
* keep upload deduplication within its source ([cf1c560](https://github.com/EffizienzAgent/docarchive/commit/cf1c560f1e83afe3426834180ccbc877399efe40))
* let people confirm e-invoice suggestions without other changes ([162a356](https://github.com/EffizienzAgent/docarchive/commit/162a35641163e98039f845b1a228057e22888f00))
* measure retention calls after both documents are archived ([4db3858](https://github.com/EffizienzAgent/docarchive/commit/4db3858ddfe686560cd5408aacfca4e8ccb3b9d9))
* quote translation with a comma ([392fd7c](https://github.com/EffizienzAgent/docarchive/commit/392fd7c09d06f7a2ca9c8c515776b27c51aa2a1c))
* read pinned versions with digits in their names ([0f829c3](https://github.com/EffizienzAgent/docarchive/commit/0f829c355a0d0c52827a051a553c0387c240cd59))
* register protect_object under the final s3storage object hooks ([f23a057](https://github.com/EffizienzAgent/docarchive/commit/f23a0579796be0b88865fc248fa24dc91b99be4f))
* remove leftover merge markers in the README ([de448a7](https://github.com/EffizienzAgent/docarchive/commit/de448a712f82846aed9b1f384d136cbf0b049956))
* resolve translation merge ([3b6313f](https://github.com/EffizienzAgent/docarchive/commit/3b6313f1fa3cc86ce672e5a1c7c9ddccfa2d21b1))
* review findings on permissions, targets and source fields ([1ff35ba](https://github.com/EffizienzAgent/docarchive/commit/1ff35ba63bf11cfa45d7535c5c0852891d620427))
* s3storage object-change hook and uploads on unsaved documents ([e0c997e](https://github.com/EffizienzAgent/docarchive/commit/e0c997e9a7ad0fd9731fc82008cf5db80edcae28))
* security review findings ([d7cd12e](https://github.com/EffizienzAgent/docarchive/commit/d7cd12e3ac9d008ca52a0b3d49c6ae08d1318543))
* skip locked documents and trust the recorded version ([91f964e](https://github.com/EffizienzAgent/docarchive/commit/91f964e416d94acac734e59baf1d528ec780abb8))
* strip deletion stubs fully and tighten guards ([94de39b](https://github.com/EffizienzAgent/docarchive/commit/94de39b86814e1f5a968eeec82cd7af72117f621))
* treat e-invoice company and type as suggestions ([fb0d07d](https://github.com/EffizienzAgent/docarchive/commit/fb0d07d5ca0bf23c4488dcd82f58a011fc01e78e))
* triage fields on the left, preview on the right ([60a3214](https://github.com/EffizienzAgent/docarchive/commit/60a3214236bb90c2604d59fe56e67b189b99f5d8))
* triage sends only changed fields and refuses stale saves ([4057652](https://github.com/EffizienzAgent/docarchive/commit/4057652c3313ec95de14151caccd5ee1fc603ad6))
* unique file names in upload tests ([25928d5](https://github.com/EffizienzAgent/docarchive/commit/25928d5c61e6b8b69e5017ae12cafc17365cf06f))

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
