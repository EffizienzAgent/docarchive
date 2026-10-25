# Development

## Tests

```
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app docarchive
```

CI runs the suite on a Frappe v16 bench at the commits pinned in `.github/versions.env`. With `DOCARCHIVE_TEST_S3_ENDPOINT`, `DOCARCHIVE_TEST_S3_ACCESS_KEY` and `DOCARCHIVE_TEST_S3_SECRET_KEY` set, the tests also run against a real S3 server (CI uses MinIO with Object Lock buckets; the MinIO root key also serves as the archive admin key).
