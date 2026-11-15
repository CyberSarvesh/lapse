# Lapse

> Snap a photo of any warranty, insurance policy, passport or subscription. Lapse reads the expiry date and reminds you before it lapses. While it waits, nothing runs and it costs almost nothing.

Built for the **AWS Weekend Challenge: "No Pipeline, No Problem"**.

## Architecture

- **Frontend:** Single-page plain HTML app ([web/index.html](web/index.html)) hosted on S3
- **API:** Amazon API Gateway (HTTP API) + Python 3.13 `arm64` Lambdas
- **Storage:** Single DynamoDB table (on-demand + TTL) and two S3 buckets (uploads + web)
- **AI Extraction:** Amazon Bedrock vision model (falls back to `NEEDS_DATE` for manual entry if unreadable)
- **Scheduler & Human Loop:** AWS Step Functions Standard workflow ([statemachine/lapse.asl.json](statemachine/lapse.asl.json)) using `Wait` states, 300-day continue-as-new splitting, and `waitForTaskToken`
- **Reminders:** Amazon SES emails with one-time action links and a confirm-before-acting page (`GET /action` → `POST /action`)

## Local Development (LocalStack)

1. Start **Docker Desktop** and bring up LocalStack:
   ```powershell
   docker compose up -d
   ```
2. Build and deploy to LocalStack (`LocalMode=true` uses a deterministic extractor instead of Bedrock, and `DemoMode=true` shortens waits to 60–90 seconds):
   ```powershell
   samlocal build
   samlocal deploy --stack-name lapse-local --resolve-s3 --no-confirm-changeset --capabilities CAPABILITY_IAM --parameter-overrides LocalMode=true DemoMode=true LambdaArchitecture=x86_64
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

The last command uploads `web/index.html` to the stack's web bucket with the API URL filled in and prints the site URL. If your account has S3 Block Public Access turned on at the account level, the bucket policy is rejected; in that case open the page locally with `python scripts/serve_web.py` instead.

Before deploying:
- Enable model access in Amazon Bedrock for the `BedrockModelId` you use. It must accept image and PDF input (default: Claude Haiku 4.5 via the `us.` inference profile). If the model can't be called, uploads fall back to "needs a date" and the error is logged in the extractor's CloudWatch logs.
- Verify your `SenderEmail` (and test recipient if in the SES sandbox) in Amazon SES.

For a live demo, set `DemoMode=true` during `sam deploy`.

## Running Unit Tests

```bash
python -m unittest discover -s tests -v
```
