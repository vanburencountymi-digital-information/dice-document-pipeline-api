# CHANGELOG

<!-- version list -->

## v5.0.0 (2026-10-22)

### Bug Fixes

- Validate limit values from admin panel before saving
  ([#31](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/31),
  [`6a6e137`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/6a6e13703155540cf23133f51289477248ad4184))

### Features

- Add API throttling (per-account limits, failed-login lockout, per-IP backstop)
  ([#31](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/31),
  [`6a6e137`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/6a6e13703155540cf23133f51289477248ad4184))

### Breaking Changes

- GET /api/status/ is removed; callers read their limits from the X-RateLimit-* response headers
  instead.


## v4.6.0 (2026-10-22)

### Documentation

- Add plain-English admin guide; test Organization admin
  ([#29](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/29),
  [`1450fba`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/1450fba5d4f06affcd0f18c368c6c70832a2b1ea))

- Add worker test steps, IAM and scheduler checks to staging guide
  ([#30](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/30),
  [`9a0d8a9`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/9a0d8a930cfa14be63d397e74d5b587692a5cb16))

- Restructure staging deploy guide as a Terraform starting point
  ([#30](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/30),
  [`9a0d8a9`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/9a0d8a930cfa14be63d397e74d5b587692a5cb16))

### Features

- Add draft Terraform for the production Google Cloud setup
  ([#30](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/30),
  [`9a0d8a9`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/9a0d8a930cfa14be63d397e74d5b587692a5cb16))


## v4.5.1 (2026-10-15)

### Bug Fixes

- Retry DB writes after a dropped connection during long pipeline steps (DIC-2416)
  ([#28](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/28),
  [`44f3704`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/44f37047a9d2566adb46db0d7bd0f27e90657d58))

- Retry DB writes after a dropped connection during long pipeline …
  ([#28](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/28),
  [`44f3704`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/44f37047a9d2566adb46db0d7bd0f27e90657d58))


## v4.5.0 (2026-10-15)

### Documentation

- Update staging-deploy docs to reflect neon
  ([#26](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/26),
  [`96cfe2f`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/96cfe2fc188ab1fa203ccdb11d4f952a8c28946c))

### Features

- Add per-account rate limiting with admin-adjustable tiers
  ([#27](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/27),
  [`c909273`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/c9092731541709ca078a6addc427007b314939ec))


## v4.4.1 (2026-10-14)

### Bug Fixes

- Add CONN_HEALTH_CHECKS for Neon
  ([#25](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/25),
  [`27998c0`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/27998c0546d1f8f79e6a0fb015f41cd12d8431e2))


## v4.4.0 (2026-10-14)

### Features

- Named tokens and token + webhooks secret management in admin
  ([#24](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/24),
  [`04f7e55`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/04f7e559b589fd2e263fe5640568be769cd29923))


## v4.3.0 (2026-10-14)

### Features

- Add Organization and Service Account Creation to Admin Panel
  ([#23](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/23),
  [`6d334eb`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/6d334eb95ca2fa4cca55f1da399fd8a1c93b564a))


## v4.2.0 (2026-10-06)

### Features

- Allow document download to return files
  ([`6ccc7fb`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/6ccc7fb4ccf52a6f34d7bde3a201333af2d2a947))


## v4.1.1 (2026-10-06)

### Bug Fixes

- Filepicker was not working in test tool
  ([`4a761a6`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/4a761a6c325fa4f033661719fcf87fcd34abac87))


## v4.1.0 (2026-10-06)

### Features

- Add DRF Spectacular to create an easy docs + test endpoint
  ([`d860ccf`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/d860ccf3a2a46c0fbe15ddfd950e145a2dc831b8))


## v4.0.0 (2026-09-29)

### Features

- Split JobStatus into compliant/noncompliant/error/skipped
  ([#19](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/19),
  [`e916aa0`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/e916aa05bf42164a2d5521b48b13c2a72315348c))

### Breaking Changes

- The `status` values "complete" and "failed" no longer exist; consumers must handle "compliant",
  "noncompliant", "error" and "skipped".


## v3.3.0 (2026-09-28)

### Features

- Add pipeline version to webhook payloads
  ([`a8c3697`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/a8c3697e08486a379d9622f54278c6372148c359))


## v3.2.0 (2026-09-28)

### Features

- Expose pipeline_version in RemediationSerializer
  ([`dddd6bf`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/dddd6bf6e37d1b68ce68c5203aa38517b392acef))


## v3.1.0 (2026-09-25)

### Features

- **CI/CD**: Setup automatic deploys on cloud run
  ([`9989ed4`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/9989ed4091335a8db10c571e6ce2329c586557c0))


## v3.0.0 (2026-09-25)

### Features

- Add deployable image with gunicorn and whitenoise
  ([`65c7735`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/65c7735a135448ba543830643c8749a3d210d8ed))


## v2.3.1 (2026-09-23)

### Bug Fixes

- Bump sqlparse from 0.5.5 to 0.6.0
  ([#11](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/pull/11),
  [`bcb591e`](https://github.com/vanburencountymi-digital-information/dice-document-pipeline-api/commit/bcb591e2c275c3ff4b288522cadfd64b3b63c1b1))


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
