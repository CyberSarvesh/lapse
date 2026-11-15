# Lapse — Project Plan
AWS Weekend Challenge: "No Pipeline, No Problem"

## 1. One-line pitch
Snap a photo of any warranty, insurance policy, passport or subscription. Lapse reads the expiry date and reminds you before it lapses. While it waits, nothing runs and it costs almost nothing.

## 2. Problem
People miss expiry dates on warranties, insurance, passports, licences and free trials. The document sits in a drawer, the date passes, and the result is a penalty, lost coverage, a re-inspection or an unwanted charge. Calendar reminders need manual setup and nobody does it.

## 3. What the build does (user journey)
1. The user opens the web page, enters their email and uploads a photo or PDF of a document.
2. AI reads the document and extracts the item name, type, provider and expiry date.
3. The item appears in the user's list with a "days left" countdown.
4. Lapse waits until 30 days before expiry.
5. The user gets an email with two buttons: "I renewed it" and "Remind me in 7 days".
6. "Renewed" with an optional new expiry date restarts the cycle for next year. "Renewed" without a date ends it. "Snooze" waits 7 days and reminds again.
7. If the user ignores the email for 3 days, Lapse nudges again, up to 5 times, then marks the item as ignored.
8. If the AI can't find a date, the item is marked "needs a date" and the user types it in on the page. Users can also add an item manually with no photo.
9. The user can stop reminders for any item.

## 4. Services and their roles
| Service | Role |
|---|---|
| Amazon S3 | Hosts the static web page; stores uploaded documents (browser uploads directly via presigned URL) |
| Amazon API Gateway (HTTP API) | Public endpoints for the page and the email buttons |
| AWS Lambda | Small single-purpose functions: get upload URL, extract, schedule, send reminder, handle action, list/add/delete items |
| AWS Step Functions (Standard) | One workflow execution per document; acts as the scheduler and waits for the user's response |
| Amazon DynamoDB | Items, upload lookups, one-time action links |
| Amazon EventBridge | Starts the workflow automatically when a file lands in S3 |
| Amazon Bedrock | Vision model reads the document and returns structured data |
| Amazon SES | Sends reminder emails |
| AWS SAM | Infrastructure as code; one-command deploy |

## 5. Architecture flow
1. Page → API `GET /upload-url` → Lambda creates a "processing" item and returns a presigned S3 upload URL.
2. Browser uploads the file straight to S3.
3. S3 "Object Created" event → EventBridge rule → starts a Step Functions execution.
4. Workflow steps:
   - Extract details (Lambda + Bedrock).
   - If no date: mark item "needs date" and end.
   - Save item as active (direct DynamoDB integration, no Lambda).
   - Compute reminder time (Lambda).
   - Wait until that time (free while waiting).
   - Check the item is still active (user may have stopped it). If not, end.
   - If the expiry is very far away, start a fresh execution and end this one (1-year execution limit).
   - Send reminder email and pause, waiting for a task token, with a timeout.
   - Branch on the answer: renewed with new date → update date and loop back to save; renewed → mark renewed and end; snooze → loop to compute reminder; timeout → follow-up mode, increment count, loop or mark ignored after 5.
5. Email button → API `GET /action` shows a confirmation page → user confirms → `POST /action` → Lambda resumes the workflow with the user's choice.

## 6. Data model (one DynamoDB table, PK + SK, on-demand, TTL enabled)
- **Item:** keyed by user email + item ID. Holds name, type, provider, expiry date, file location, status, current execution ID and timestamps.
- **Upload lookup:** keyed by item ID. Maps an uploaded file back to the user's email. Expires after 1 day.
- **Login token / session:** keyed by the SHA-256 of the token. Login tokens last 15 minutes and are deleted on use; sessions last 30 days.
- **Action link:** keyed by a short random ID. Holds the workflow task token and item info. Deleted after one use; expires after 8 days.
- **Statuses:** PROCESSING, NEEDS_DATE, ACTIVE, RENEWED, IGNORED, DELETED.

## 7. API routes
| Route | Purpose |
|---|---|
| POST /auth/start | Email a one-time sign-in link (same answer whether or not the address is known) |
| POST /auth/verify | Exchange the link's token for a session token |
| POST /auth/logout | End the session |
| GET /upload-url | Get a presigned upload URL for a file type (signed in) |
| GET /items | List the signed-in user's items (deleted ones hidden), sorted by expiry |
| POST /items | Add an item manually, or supply the date for a "needs date" item; starts a workflow |
| DELETE /items/{itemId} | Stop reminders (soft delete) |
| GET /action | Show confirm page for an email button |
| POST /action | Confirm the choice and resume the workflow |

## 8. Front end
A single HTML page, no framework:
- Sign in with email: a one-time link, no password (D26); the session is remembered in the browser
- File picker (JPG, PNG, WEBP, PDF, under 3.5 MB) and upload button
- "Add manually" form
- List of items as cards with a status chip ("23 days left", "reading…", "needs a date", "renewed ✔")
- An inline date form on "needs a date" cards
- A "Stop reminders" button
- Auto-refresh for a short time after uploading

## 9. Configuration
- Sender email (SES-verified)
- Bedrock model ID (vision-capable)
- Demo mode on/off. When on: first reminder after ~90 seconds, snooze and follow-up after ~60 seconds, 3-minute response window. When off: 30 days before expiry, 7-day snooze, 1-day follow-up, 3-day response window.
- Lead days (default 30)

## 10. Build milestones
Each milestone must work before starting the next.
1. **Setup:** AWS account and region chosen, Bedrock model access enabled, SES sender and test recipient verified, SAM project created.
2. **Storage and data:** table and both buckets deploy.
3. **Workflow skeleton:** state machine deploys; a manual start with a fake item reaches the Wait state and appears in the console.
4. **Email loop:** reminder email arrives; both buttons resume the workflow correctly; a reused link shows "expired".
5. **Upload and AI:** uploading a real document photo starts the workflow automatically and extracts a correct date.
6. **Fallbacks:** an unreadable document becomes "needs a date"; entering the date manually schedules it.
7. **Front end:** page hosted on S3 and showing live status.
8. **Edge cases:** ignore path (follow-ups, then IGNORED), stop reminders, renewed with new date loops, far-future expiry triggers the continue step.
9. **Docs:** README, architecture diagram, screenshots, cost note.
10. **Demo:** record the video with demo mode on.

## 11. Acceptance checklist
- [ ] One command deploys everything; no servers, containers or pipelines
- [ ] Upload → correct date extracted → item shows as active with countdown
- [ ] Reminder email arrives on schedule
- [ ] Snooze loops and sends another reminder
- [ ] Renewed (no date) ends the workflow and marks the item renewed
- [ ] Renewed (with date) updates the date and restarts the cycle
- [ ] Ignoring the email triggers follow-ups and ends as IGNORED after 5
- [ ] Stopping reminders ends the workflow at its next check
- [ ] Each email link works once only
- [ ] Unreadable document → "needs a date" → manual date → scheduled
- [ ] Nothing runs while waiting (verify there are no scheduled jobs or polling)

## 12. Demo video (under 3 minutes)
| Time | Content |
|---|---|
| 0:00–0:20 | Personal story of a missed renewal; one-line pitch |
| 0:20–0:45 | Architecture diagram: "no cron, no servers, near-zero cost idle" |
| 0:45–1:30 | Upload a real warranty card; show the extracted date; switch to the Step Functions console showing the execution sitting in Wait |
| 1:30–2:15 | Email arrives (demo mode); click Snooze and the graph loops; click Renewed and the graph turns green |
| 2:15–2:45 | DynamoDB item shows RENEWED; cost explanation (billed per step, not per second waited) |
| 2:45–3:00 | The reusable pattern for other use cases; repo link |

Say clearly on camera that demo mode shortens 30 days to about 90 seconds.

## 13. Submission write-up outline
1. The problem (personal story)
2. What Lapse does (3 screenshots)
3. Architecture diagram and how requests and data flow
4. Why Step Functions is the scheduler (the key idea)
5. Reliability details: retries, one-time links, confirm-before-action, 1-year limit handling, AI fallback, TTL cleanup
6. Cost when idle
7. How to deploy it yourself with SAM; how to reuse the pattern
8. Known limitations and what's next

## 14. Out of scope
Passwords and social login, SMS or WhatsApp, calendar export, mobile app, multi-language, CI/CD.