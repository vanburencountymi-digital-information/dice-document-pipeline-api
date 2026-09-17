# CHANGELOG

<!-- version list -->

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
