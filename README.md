# Lapse

> Snap a photo of any warranty, insurance policy, passport or subscription. Lapse reads the expiry date and reminds you before it lapses. While it waits, nothing runs and it costs almost nothing.

Built for the **AWS Weekend Challenge: "No Pipeline, No Problem"**.

## Architecture

- **Frontend:** Single-page plain HTML app ([web/index.html](web/index.html)) in a private S3 bucket, served over HTTPS at the API's own address (`GET /`), or by CloudFront with `EnableCdn=true`
- **API:** Amazon API Gateway (HTTP API) + Python 3.13 `arm64` Lambdas
- **Storage:** Single DynamoDB table (on-demand + TTL) and two S3 buckets (uploads + web)
- **AI Extraction:** Amazon Bedrock vision model when the account has model access, otherwise Amazon Textract OCR with a scored date finder ([src/common/doc_reader.py](src/common/doc_reader.py)). Unlabelled dates are offered for confirmation; unreadable documents fall back to `NEEDS_DATE`
- **Scheduler & Human Loop:** AWS Step Functions Standard workflow ([statemachine/lapse.asl.json](statemachine/lapse.asl.json)) using `Wait` states, 300-day continue-as-new splitting, and `waitForTaskToken`
- **Reminders:** Amazon SES emails with one-time action links and a confirm-before-acting page (`GET /action` → `POST /action`)
- **Push notifications:** Web Push (VAPID) to every device the user turns on, so reminders arrive even when email goes to Spam; the email and push channels back each other up
- **Reminder timing & calendar:** each item chooses when the first reminder goes out (1 day to 3 months before), and a private, revocable iCalendar feed (`/calendar/<token>.ics`) puts every expiry date into Google, Apple or Outlook Calendar

## Local Development (LocalStack)

1. Start **Docker Desktop** and bring up LocalStack:
   ```powershell
   docker compose up -d
   ```
2. Build and deploy to LocalStack (`LocalMode=true` uses a deterministic extractor instead of Bedrock, and `DemoMode=true` shortens waits to 60–90 seconds):
   ```powershell
   samlocal build
   samlocal deploy --stack-name lapse-local --resolve-s3 --no-confirm-changeset --capabilities CAPABILITY_IAM --parameter-overrides LocalMode=true DemoMode=true LambdaArchitecture=x86_64 AppUrl=https://127.0.0.1:8443
   ```
3. Verify the SES sender identity in LocalStack:
   ```powershell
   awslocal ses verify-email-identity --email-address noreply@lapse.local
   ```
4. Serve the frontend over local HTTPS and open `https://127.0.0.1:8443` (accept the self-signed cert in your browser), then paste the `ApiUrl` stack output into **Connection settings**:
   ```powershell
   python scripts/serve_web.py
   ```

## Deploy to AWS

```bash
sam build
sam deploy --guided
python scripts/deploy_web.py --stack-name <your-stack-name>
```

The last command uploads `web/index.html` to the stack's private web bucket with the API URL filled in and prints the HTTPS address of the app (`https://<api-id>.execute-api.<region>.amazonaws.com/`). With `EnableCdn=true` the page is served by CloudFront instead and the script also refreshes its cache. New AWS accounts can't create CloudFront distributions until AWS Support verifies them, which is why it is off by default.

Before deploying:
- Photo reading works out of the box with Amazon Textract (about $1.50 per 1,000 pages). Optionally, enable model access in Amazon Bedrock for the `BedrockModelId` you use; it is tried first. It must accept image and PDF input (default: Amazon Nova Lite via the `us.` inference profile, about $0.06 per million input tokens, so each document costs a tiny fraction of a cent). If the model can't be called, uploads fall back to "needs a date" and the error is logged in the extractor's CloudWatch logs.
- Verify your `SenderEmail` (and test recipient if in the SES sandbox) in Amazon SES.

For a live demo, set `DemoMode=true` during `sam deploy`.

### Browser notifications (optional, recommended)

```bash
python scripts/setup_push.py --region us-east-1      # once: creates the key in SSM, prints VapidPublicKey=...
python scripts/build_push_layer.py --arch arm64      # before each deploy: Lambda layer with `cryptography`
sam deploy ... --parameter-overrides VapidPublicKey=<printed key>
python scripts/deploy_web.py --stack-name <your-stack-name>
```

Then sign in and press **Turn on** under "This device". On iPhone, add Lapse to the Home Screen first (Share → Add to Home Screen); iOS only allows notifications for installed web apps.

## Privacy and sign-in

- **Sign-in:** enter your email and Lapse sends a one-time link (valid 15 minutes). There's no password. The API only ever serves the signed-in user's items, so knowing someone's email address isn't enough to see them. In LocalMode (LocalStack doesn't deliver email) the page shows the link directly.
- **Documents:** the uploaded photo or PDF is deleted as soon as the date is read; only the name, provider and date are kept. A 1-day lifecycle rule removes anything left behind.
- **HTTPS:** the page is served only over HTTPS, with HSTS, `nosniff`, frame-blocking and a strict referrer policy, so the sign-in token in the link never travels in clear text. To use your own domain, add it to the distribution with an ACM certificate and deploy with `AppUrl=https://your.domain`.

## Running Unit Tests

```bash
python -m unittest discover -s tests -v
```
