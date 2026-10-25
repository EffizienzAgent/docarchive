# Changelog

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
