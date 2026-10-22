# Admin guide: organizations and accounts

For anyone who runs the pipeline. No coding or terminal needed. Log in at `APP_URL/admin/`.

An **organization** is a customer. A **service account** is one connection to the API (e.g. one website). Each account has one or more **tokens**, which work like passwords.

## Add an organization and an account

1. **Organizations** → **Add**. Enter a name. Save.
2. **Service accounts** → **Add**.
3. Pick the organization. Enter a name for the account (e.g. `wordpress-prod`) and a name for its first token (e.g. `first-token`).
4. Save. The token appears at the top of the page, **only this once**. Copy it now and send it to the customer through a safe channel.

New accounts start with the default limits (10 documents at once, 100 submissions a day). Change them on the account's page if needed.

## Lost a token, or need another

Open the account → **Issue a new token** (top right). Older tokens keep working.

To switch a token off: **Service account tokens** → tick it → delete. Whoever uses that token stops working straight away.

## Webhook secret

Open the account to see it. **Regenerate webhook secret** (top right) makes a new one. Whoever checks our webhooks must be updated, or their checks will fail.

## Change an account's limits

Open the account and edit:

- **Maximum submissions allowed at once, queued and working**: documents waiting or being worked on.
- **Submission limit per time period** and **Time period**: e.g. 100 per day.
- **Service Account has no limits**: skips both. Use only for large one-off jobs. Untick it to go back to the saved numbers.

Leaving a number blank does not mean "no limits". Tick the box.

## Backfill account

A backfill sends a whole website's old documents at once, so it gets its own account.

1. Add the organization (if it's new), then add an account named for the job (e.g. `backfill`), as above.
2. Open the account. Either raise its limits, or tick **Service Account has no limits**.
3. Set the backfill runner's own "in flight" cap at or below the account's **Maximum submissions allowed at once**. If the runner's cap is higher, the account will turn away its extra documents.
4. When the backfill is finished, delete the account or untick **no limits**.

More: [ADR 0030](adrs/0030-per-account-limits.md).

## Deleting

Deleting an account also deletes its tokens, so they stop working. This can't be undone.
