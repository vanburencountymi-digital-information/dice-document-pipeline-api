# CHANGELOG

<!-- version list -->

## v2.3.0 (2026-09-23)

### Bug Fixes

- Copy block needs to own files to be able to run inside of container
  ([`f8f2193`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/f8f2193e888638cbc24eaf2c200f1ff50b24c44a))

### Features

- Add true workers and task queues via django-tasks-db
  ([`5fa61b8`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/5fa61b811c28c1eb995c5702b8f9fdf0d848dffe))

- Make pipeline steps idempotent for task redelivery
  ([`d902515`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/d902515c33bb045d6f09487e939de96446d58f5b))

### Testing

- Add Django test battery to CI/CD for PRs targeting main
  ([`5f464d4`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/5f464d4b94fc5ef9548d7ffb8d3caed79011cb49))


## v2.2.0 (2026-09-22)

### Features

- Add live OCR optional test battery for regressions testing
  ([#12](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/12),
  [`0f219e3`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/0f219e31eaee7f5ecc9e32c5f82fabd78c208553))

- OCR regressions test battery
  ([#12](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/12),
  [`0f219e3`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/0f219e31eaee7f5ecc9e32c5f82fabd78c208553))

- Update OCR regression diffs to include human-readable, colored diff history
  ([#12](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/12),
  [`0f219e3`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/0f219e31eaee7f5ecc9e32c5f82fabd78c208553))


## v2.1.0 (2026-09-21)

### Bug Fixes

- Gh releases should authenticate via short-lived app token
  ([`0c3e288`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/0c3e288de8a4d89eec1ba020c3c352783784f885))

- Missing file from sentry release
  ([`908745b`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/908745b64860d55bba860237e758d72cac623be8))

- Update release.yml to work with Private GH App for semantic release package / ruleset
  ([`92d62cf`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/92d62cf88560be55db28c2dcc108a8e21514ebe5))

### Chores

- Add omitted field from RemediationSerializer test
  ([`ca6ecd0`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/ca6ecd04452a42dc1209383022f145ab1d5b498e))

- Add testing settings
  ([`a7af55b`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/a7af55bc50b05fabf881c0658efc6e7a4cb75ba1))

### Continuous Integration

- Fix semantic release token error
  ([`834dde8`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/834dde8f63f94fe227bc91e3f1dc3e86dea229db))

### Features

- Add sentry_sdk for error monitoring
  ([`4a14f59`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/4a14f599d9daba301e739cd454d6d87d221c01f3))

- Swap FileSystem storage for S3 compatible bucket storage
  ([`6ceaead`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/6ceaead88b783cb261f60d2aa4c00c3098f911f8))


## v2.0.0 (2026-09-17)

### Chores

- Simplify making migrations
  ([`e15e5d8`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/e15e5d82b48492ad40dee48abee30f22c363b8ac))

### Features

- Switch to django-rest-knox for auth tokens and drop support for DRF plaintext tokens
  ([`862e8e8`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/862e8e853199f2ac01d8ec41422d7cbb36cc8f85))

### Breaking Changes

- Any issued plaintext tokens will be immediately invalidated. Issue new tokens via django-admin or
  shell. See ADR-0016 for justification


## v1.5.0 (2026-09-16)

### Features

- Add webhook support to notify callers when remediation is complete
  ([`b52a881`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/b52a88184fd99d4f295c045117e12bf249d120c4))


## v1.4.1 (2026-09-16)

### Bug Fixes

- Add a webhook secret to service accounts
  ([`f4b0533`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/f4b0533c8967420e8a087929498d5f5a0c516bf0))


## v1.4.0 (2026-09-16)

### Features

- Add an endpoint for users to fetch remediated documents from the server
  ([`c450965`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/c4509654336f1f7ca884c9def5b81a6c332804c1))


## v1.3.0 (2026-09-16)

### Features

- Add final output uri to Remediation model
  ([`aede6a4`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/aede6a4828c83513d9e6090eb7d3858cf3db5ffe))


## v1.2.0 (2026-09-16)

### Features

- Add versioned retry for failed remediations
  ([`33a2961`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/33a2961b184ec0e438a14310d5d9ac9b3f64e480))


## v1.1.0 (2026-09-16)

### Features

- Continue the remediation pipeline past a failed step
  ([`957b1eb`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/957b1ebb0608c97fcece9d889e7fa7783da2137e))


## v1.0.0 (2026-09-16)

- Initial Release
