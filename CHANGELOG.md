# Changelog

## [0.3.0](https://github.com/EffizienzAgent/docarchive/compare/v0.2.0...v0.3.0) (2026-10-25)


### Features

* cover attachments in legal holds and deletion ([531d23c](https://github.com/EffizienzAgent/docarchive/commit/531d23cc4adbe64641304de37c0895d24cf1c6f7))
* filterable documents panel and per-file upload dates ([952f7ef](https://github.com/EffizienzAgent/docarchive/commit/952f7ef8d1db5b791fb6eba63edcdb631fad8c19))
* hold at archive time, admin key for later holds ([42dde9f](https://github.com/EffizienzAgent/docarchive/commit/42dde9f5136f011dd9224d37609de2b2fe340f32))
* legal hold and deletion of expired documents ([b98d1d3](https://github.com/EffizienzAgent/docarchive/commit/b98d1d39e9303911fe297cbdb2877af505edc242)), closes [#11](https://github.com/EffizienzAgent/docarchive/issues/11)
* manual retention extension and retention overview ([98d1bf4](https://github.com/EffizienzAgent/docarchive/commit/98d1bf45169fbea488bfc4b6b6eb7e83cdc8fdb7))
* merge attachments into a known upload ([3f4003e](https://github.com/EffizienzAgent/docarchive/commit/3f4003ec8aff1e0dd183c4f491568a48ef86d7df))
* multiple files per document ([f99c99a](https://github.com/EffizienzAgent/docarchive/commit/f99c99a685ccc16fc984e14cc41a6c235934e6b0))
* scripts to set and check archive bucket policies ([ad53e73](https://github.com/EffizienzAgent/docarchive/commit/ad53e7300b5a9be89e0f9ff46706827bb41d4708))
* scripts to set and check archive bucket policies ([e3de81b](https://github.com/EffizienzAgent/docarchive/commit/e3de81baf12dc9edffe0aff72ff1799c1b746000))
* show legal hold in panel and retention overview ([1c53423](https://github.com/EffizienzAgent/docarchive/commit/1c53423da681ca27b401e0acab3076c7393c45ee))
* triage dialog for documents that need work ([50250be](https://github.com/EffizienzAgent/docarchive/commit/50250bee288c3c4f5c4ac8291df41a2e6f03911c))


### Bug Fixes

* bound and harden manual retention extension ([5523ff7](https://github.com/EffizienzAgent/docarchive/commit/5523ff7930f6c19c20a3926043abc4f5b030f8f6))
* check pending extensions directly in the deleted test ([ecf00a9](https://github.com/EffizienzAgent/docarchive/commit/ecf00a9132321451109c8439d92d71ba63920a72))
* guard filter storage and hide deleted documents ([5b17e27](https://github.com/EffizienzAgent/docarchive/commit/5b17e27669eb5ac8e0ace7ab8e0cb25b67a24c42))
* keep upload deduplication within its source ([cf1c560](https://github.com/EffizienzAgent/docarchive/commit/cf1c560f1e83afe3426834180ccbc877399efe40))
* measure retention calls after both documents are archived ([4db3858](https://github.com/EffizienzAgent/docarchive/commit/4db3858ddfe686560cd5408aacfca4e8ccb3b9d9))
* quote translation with a comma ([392fd7c](https://github.com/EffizienzAgent/docarchive/commit/392fd7c09d06f7a2ca9c8c515776b27c51aa2a1c))
* resolve translation merge ([3b6313f](https://github.com/EffizienzAgent/docarchive/commit/3b6313f1fa3cc86ce672e5a1c7c9ddccfa2d21b1))
* strip deletion stubs fully and tighten guards ([94de39b](https://github.com/EffizienzAgent/docarchive/commit/94de39b86814e1f5a968eeec82cd7af72117f621))
* triage fields on the left, preview on the right ([60a3214](https://github.com/EffizienzAgent/docarchive/commit/60a3214236bb90c2604d59fe56e67b189b99f5d8))
* triage sends only changed fields and refuses stale saves ([4057652](https://github.com/EffizienzAgent/docarchive/commit/4057652c3313ec95de14151caccd5ee1fc603ad6))
* unique file names in upload tests ([25928d5](https://github.com/EffizienzAgent/docarchive/commit/25928d5c61e6b8b69e5017ae12cafc17365cf06f))

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
