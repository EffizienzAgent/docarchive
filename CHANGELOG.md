# Changelog

## [0.2.0](https://github.com/EffizienzAgent/docarchive/compare/v0.1.1...v0.2.0) (2026-10-25)


### Features

* extend contract retention and track the lock mode ([ddf4165](https://github.com/EffizienzAgent/docarchive/commit/ddf4165f7dd798beb00215d4d11eb3a44abc4113))
* nightly archive run to Object Lock storage ([553c7fc](https://github.com/EffizienzAgent/docarchive/commit/553c7fc176c18feeb52ac18ac8db7cc6cda20cab))
* source record fields and a single content function ([db2eab5](https://github.com/EffizienzAgent/docarchive/commit/db2eab576078044abaa8bf47354d6e029e162b84))
* upload API for integrations ([0c77a42](https://github.com/EffizienzAgent/docarchive/commit/0c77a42e0a7b3a72b80a07ac3223c44feaa0c2fa))


### Bug Fixes

* harden retention extension and mode sync after security review ([7d1823e](https://github.com/EffizienzAgent/docarchive/commit/7d1823e57ece344f5eb7ca64093d79501f561100))
* harden the archive run after security review ([c16e3dd](https://github.com/EffizienzAgent/docarchive/commit/c16e3dd431166d8a4f8cf55615235f6afdcbb1e8))
* harden the upload API after security review ([84fd387](https://github.com/EffizienzAgent/docarchive/commit/84fd387ddafc40bc242986b750b098d800eb7ebc))
* read pinned versions with digits in their names ([0f829c3](https://github.com/EffizienzAgent/docarchive/commit/0f829c355a0d0c52827a051a553c0387c240cd59))
* skip locked documents and trust the recorded version ([91f964e](https://github.com/EffizienzAgent/docarchive/commit/91f964e416d94acac734e59baf1d528ec780abb8))

## [0.1.1](https://github.com/EffizienzAgent/docarchive/compare/v0.1.0...v0.1.1) (2026-10-24)


### Bug Fixes

* register protect_object under the final s3storage object hooks ([f23a057](https://github.com/EffizienzAgent/docarchive/commit/f23a0579796be0b88865fc248fa24dc91b99be4f))

## 0.1.0 (2026-10-24)


### Features

* business documents with lock, storage targets and host hooks ([4c76a87](https://github.com/EffizienzAgent/docarchive/commit/4c76a87e49214feef4adf2ac3dd1dc685790ca67))
* contract end date may only move later after archive start ([9128e4c](https://github.com/EffizienzAgent/docarchive/commit/9128e4c69f2e378e1b8960b1380b30445c5d3637))
