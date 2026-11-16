# Lapse — Design Decisions
Each entry records what was decided, the alternatives, and why. Use this when anything in the plan seems odd or needs changing.

## D1. Step Functions is the scheduler
- **Decision:** One Standard workflow execution per document; a Wait state holds until reminder time.
- **Alternatives:** A daily cron Lambda scanning the table; EventBridge Scheduler one-time schedules; DynamoDB TTL tricks.
- **Why:** No polling or scanning, so nothing runs while waiting. Waiting, asking the human, branching and looping all live in one visual workflow that judges can see. This is the project's core originality. EventBridge Scheduler could handle the timer alone, but the human-response loop would then be spread across several functions.

## D2. Standard workflows, not Express
- **Decision:** Standard type.
- **Why:** Express workflows last at most 5 minutes and don't support waiting for task tokens. Standard bills per state transition, not for time spent waiting, so a months-long wait is effectively free.

## D3. Email buttons resume the workflow via task tokens
- **Decision:** The reminder step pauses with "wait for task token"; the button handler sends success with the user's choice.
- **Why:** The workflow stays in control of the timeline, and the user's choice drives a Choice state. No status polling is needed.

## D4. Short IDs in email links, tokens stored in DynamoDB
- **Decision:** Links carry a random short ID; the actual token sits in the table and is deleted after one use, with a TTL backup.
- **Why:** Tokens are very long and sensitive. Short one-time links are cleaner and safer, and a reused link can show a friendly "expired" page.

## D5. Confirm page before acting (GET shows, POST acts)
- **Decision:** Clicking an email button opens a confirmation page; only the form submit resumes the workflow.
- **Why:** Email security scanners pre-fetch links, which could otherwise "click" Renewed automatically. It also gives a natural place for the optional new-expiry-date field.

## D6. Browser uploads directly to S3 with a presigned URL
- **Alternatives:** Upload through API Gateway and Lambda.
- **Why:** API Gateway and Lambda have payload limits and add cost. A direct upload is faster, cheaper and the standard serverless pattern.

## D7. EventBridge starts the workflow from the S3 event
- **Alternatives:** An S3 notification to a Lambda that starts the workflow.
- **Why:** It removes a glue Lambda; fewer moving parts means better craft. S3 notifications can't target Step Functions directly, but EventBridge can.

## D8. File names are random IDs, and the email is looked up separately
- **Decision:** Uploads are stored as `uploads/<itemId>.<ext>`; an "upload lookup" record maps the item ID to the email.
- **Why:** Putting emails in object keys causes encoding problems and leaks personal data into file paths.

## D9. Amazon Bedrock vision model for extraction
- **Alternatives:** Amazon Textract (OCR) plus date parsing rules.
- **Why:** One call returns clean structured JSON. It understands context (e.g., "2-year warranty from purchase date" → computes the expiry) and handles messy photos. Textract would need extra logic to find the right date among many.

## D10. AI failure never breaks the flow
- **Decision:** If extraction fails or finds no date, the item becomes NEEDS_DATE and the user enters the date on the page. Manual entry is also available without any photo.
- **Why:** Reliability is judged. The app must work end to end even when the AI is wrong, slow or unavailable, including during the demo.

## D11. A single DynamoDB table, on-demand billing
- **Why:** Few access patterns, all keyed by user or ID. On-demand scales to zero cost. A single table keeps the template small.

## D12. Soft delete
- **Decision:** "Stop reminders" sets status to DELETED instead of removing the record.
- **Why:** The waiting workflow checks the item's status after each Wait and ends cleanly if it isn't active. It avoids missing-record errors, and nothing has to stop executions remotely.

## D13. Handling the 1-year execution limit ("continue as new")
- **Decision:** A single wait is capped at about 300 days. If the reminder is further away, the workflow starts a fresh execution of itself carrying the item, then ends.
- **Why:** Standard executions can't exceed 1 year. The cap leaves headroom for snoozes and follow-ups within the same execution. Handling this shows deep understanding of the service.

## D14. Renewal can restart the cycle
- **Decision:** "Renewed" plus a new expiry date updates the item and loops back to the start; renewed without a date ends.
- **Why:** Insurance and subscriptions renew yearly; one upload covers years of reminders.

## D15. Follow-ups capped at 5
- **Decision:** No response within the window → follow-up reminder; after 5 ignored reminders, mark IGNORED and end.
- **Why:** It's persistent without being spammy, and every workflow is guaranteed to end eventually.

## D16. Demo mode
- **Decision:** A deploy parameter shrinks all waits to seconds or minutes.
- **Why:** The full loop must fit in a 3-minute video. It's stated openly in the video and README so it doesn't look faked.

## D17. Reminder lead time of 30 days
- **Why:** Most renewals (insurance, passports) need time for paperwork. It's configurable.

## D18. HTTP API instead of REST API
- **Why:** Cheaper, simpler, built-in CORS. No REST-only features are needed.

## D19. AWS SAM for infrastructure
- **Alternatives:** CDK, console clicks, Terraform.
- **Why:** The challenge explicitly suggests SAM. It has a native state machine resource, policy templates and a one-command deploy, and it's the easiest pattern for others to reuse. No CI/CD pipeline, in line with the challenge theme.

## D20. Least-privilege permissions per function
- **Why:** Each function gets only the access it needs (e.g., only the action handler can resume workflows). Judges notice this as craft.

## D21. Plain HTML front end on S3
- **Alternatives:** React or another framework, Amplify hosting.
- **Why:** The judging rewards a small app that works end to end. A single file has no build step and nothing to break. If account settings block public S3 hosting, opening the page locally works too.

## D22. Python, arm64 Lambdas
- **Why:** Python is simple and readable, and the AWS SDK is built in, so there are no dependencies to package. Arm64 (Graviton) is cheaper, a small detail worth mentioning.

## D23. Users prove they own their email (superseded the "email only" first version)
- **Decision:** A user's identity is their email, but only after they prove they own it with a sign-in link (D26). Every user-data route takes the email from the session, never from the request.
- **Why:** In the first version anyone who typed an address could read or stop that person's items. People photograph passports and insurance policies for this app, so they have to trust that only they can see their data.

## D24. SES sandbox accepted
- **Decision:** Stay in the SES sandbox and verify the demo recipient addresses.
- **Why:** Leaving the sandbox takes an approval process; the demo only needs verified addresses. Documented in the README.

## D26. Passwordless sign-in links, sessions in DynamoDB
- **Decision:** `POST /auth/start` emails a one-time link (15 minutes); the page exchanges it at `POST /auth/verify` for a 30-day session token sent as `Authorization: Bearer`. Login tokens and sessions are stored in the same table, keyed by the SHA-256 of the token, and every read checks `ttl` itself. `/auth/start` answers the same whether or not it sent an email, allows one email per address per minute, and the API has route throttling.
- **Alternatives:** Amazon Cognito (passwordless needs custom auth Lambdas, and the hosted UI breaks the one-page feel); signed JWTs (needs a secret to manage, and can't be revoked); a Lambda authorizer.
- **Why:** It reuses the one-time-link pattern the reminder emails already use (D4), has no passwords to leak, costs nothing idle, works the same on LocalStack, and sign-out really ends the session. One `authenticated_email()` check is the single choke point. The sign-in link's target comes from deploy config (`AppUrl`), never from the request, so nobody can make Lapse email a link that points elsewhere.

## D27. Documents are deleted as soon as they are read
- **Decision:** The extractor deletes the uploaded file right after reading it, whether extraction worked or not. A 1-day lifecycle rule on `uploads/` is the backstop, and the uploads bucket blocks all public access.
- **Why:** Lapse only needs the name, provider and date. Not keeping passport and policy photos removes the most sensitive data entirely, and is a promise the page can make plainly. If extraction fails the user types the date (D10), so the file is never needed again.

## D28. Textract OCR as the fallback reader, unlabelled dates need confirming
- **Decision:** The extractor tries Bedrock first and falls back to Amazon Textract `DetectDocumentText` when Bedrock is unavailable or finds no date. `common/doc_reader.py` turns the OCR lines into an expiry date by scoring every date on the words around it ("valid until", "expiry", "renews on" vs "issued", "date of birth", "purchase"), takes the end of a "from … to …" period, adds a warranty term ("2 years") to the purchase date, and reads 03/04/2027 in the uploader's locale order. A date with no expiry label is stored as `suggestedDate` on a NEEDS_DATE item for the user to confirm; a date already in the past becomes `expiredOn`. The page shrinks photos to a ≤2400px JPEG before upload.
- **Alternatives:** Waiting for Bedrock model access (blocked on this account); Textract `AnalyzeExpense`/`AnalyzeID` (narrower, dearer); sending the raw OCR text to an LLM (needs Bedrock again).
- **Why:** The headline feature has to work on a brand-new account. Textract is a plain AWS API with no model-access step, costs about $1.50 per 1,000 pages, and the scoring is deterministic and unit-tested. Asking for confirmation on unlabelled dates keeps a wrong guess from silently scheduling a reminder.

## D29. The page is served over HTTPS from a private bucket
- **Decision:** On AWS the web bucket is private. A small Lambda on the API's `GET /` route serves `index.html` from it with HSTS, nosniff, frame-deny and a strict referrer policy, so the app lives at the API's HTTPS address and page and API share one origin. Sign-in links point there (taken from API Gateway's `requestContext.domainName`, which must be an execute-api domain) unless `AppUrl` overrides it. `EnableCdn=true` serves the page through CloudFront with Origin Access Control instead. In `LocalMode` the bucket stays a public S3 website because LocalStack has no CloudFront.
- **Alternatives:** The S3 website endpoint (HTTP only, browsers mark it "Not secure"); CloudFront by default (refused on new accounts until AWS Support verifies them); Amplify Hosting (another service and build step).
- **Why:** Sign-in links carry a token, so the page must not travel in clear text, and a public bucket was the last public-by-policy resource. Serving from the API needs no extra service or verification, costs a Lambda call per page load, and still allows CloudFront and a custom domain later.

## D25. Region choice
- **Decision:** A region where both Bedrock (with a vision model) and SES are available; us-east-1 by default.
- **Why:** Avoids last-minute surprises with model availability.