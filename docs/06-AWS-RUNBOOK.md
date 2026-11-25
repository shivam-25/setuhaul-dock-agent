# SetuHaul — AWS runbook (AgentCore) and multi-user access
Written so you can run every step yourself. Profile: shivam2505. Region: ap-southeast-2 (SCP denies others).
Rule: local-first. Nothing here is on the demo critical path. Deploy only after the Thursday recording is banked.

## PART A — How other people get access (the design question)

DO NOT create an IAM user per person. IAM users mean long-lived keys, manual rotation,
and an AWS bill-payer relationship with people who are only using your app.
IAM is for AWS resources talking to AWS resources. App users belong in an identity pool.

Three audiences, three mechanisms:

| Audience | Mechanism | Why |
|---|---|---|
| Ops coordinators (named humans) | **Cognito user pool** -> JWT -> API Gateway JWT authorizer | real logins, MFA, revocable, no IAM user |
| Drivers (many, transient, no account) | **short-lived signed link** carrying a scoped token (driver_id + shipment_id + exp) | a driver will never register; the link IS the credential |
| Other systems (TMS, warehouse) | **API Gateway API key + usage plan**, or client-credentials JWT | machine identity, throttled, revocable per client |

ONE IAM role exists in the whole system: the AgentCore/Lambda EXECUTION role.
Humans never hold IAM credentials. This is the answer to "how do we add a user":
you add them to the Cognito pool, not to IAM.

Token scope: every token carries facility_id and role. The API filters every query by
the token's facility, so a Gurugram coordinator cannot read Jaipur's queue. Tenancy is
enforced server-side from the token, never from a request parameter.

## PART B — Step by step, in order. Verify after each step.

### B0. Preconditions (5 min)
  aws sts get-caller-identity --profile shivam2505
  aws configure get region --profile shivam2505      # expect ap-southeast-2
  VERIFY: account id printed, region is ap-southeast-2.

### B1. Confirm model access BEFORE building anything (10 min) -- do this FIRST
  aws bedrock list-foundation-models --profile shivam2505 --region ap-southeast-2 \
    --query "modelSummaries[?contains(modelId,'nova')].modelId"
  aws bedrock list-inference-profiles --profile shivam2505 --region ap-southeast-2 \
    --query "inferenceProfileSummaries[].inferenceProfileId"
  VERIFY: you can see nova models. If only apac.* inference profiles appear, you MUST
  call the inference-profile id (apac.amazon.nova-micro-v1:0), not the bare model id.
  Smoke test one call before writing code:
  aws bedrock-runtime converse --profile shivam2505 --region ap-southeast-2 \
    --model-id apac.amazon.nova-micro-v1:0 \
    --messages '[{"role":"user","content":[{"text":"reply with OK"}]}]'
  VERIFY: "OK" comes back. If this fails, STOP - everything downstream depends on it.

### B2. Database (30 min local, or RDS later)
  LOCAL (recommended for the demo):
    docker run -d --name setuhaul-pg -e POSTGRES_PASSWORD=... -p 5432:5432 postgres:16
    python scripts/seed_from_sqlite.py     # porting the given snapshot, read-only source
  VERIFY: row counts match the SQLite source table by table. Zero drift.
  RDS (only if time remains):
    - Subnet group across 2 AZs, publiclyAccessible=false
    - Security group allows 5432 ONLY from the Lambda/AgentCore security group
    - Credentials in Secrets Manager, never in env vars

### B3. Secrets (10 min)
  aws secretsmanager create-secret --profile shivam2505 --region ap-southeast-2 \
    --name setuhaul/db --secret-string '{"host":"...","user":"...","password":"..."}'
  VERIFY: get-secret-value returns it. The execution role gets secretsmanager:GetSecretValue
  on THIS SECRET ARN ONLY - never a wildcard.

### B4. Execution role (15 min)
  Trust policy: bedrock-agentcore.amazonaws.com (and lambda.amazonaws.com if you front it)
  Permissions, each scoped to one resource ARN:
    bedrock:InvokeModel                -> the two nova inference-profile ARNs
    bedrock:ApplyGuardrail             -> the guardrail ARN (if used)
    secretsmanager:GetSecretValue      -> setuhaul/db only
    logs:CreateLogStream, PutLogEvents -> its own log group only
  VERIFY: aws iam simulate-principal-policy for one allowed and one denied action.

### B5. Package and deploy the agent to AgentCore (45 min)
  - Container must listen on 0.0.0.0:8080 and expose POST /invocations and GET /ping
  - Build for linux/arm64:  docker buildx build --platform linux/arm64 -t setuhaul-agent .
  - Push to ECR, then create the AgentCore runtime pointing at the image + execution role
  VERIFY: invoke the runtime with one test payload and get a reply.
  NOTE: first invocation is a COLD START measured in seconds. It lands on the FIRST
  message of a spike. Keep a warming ping if the demo is live.

### B6. Front door and auth (45 min)
  - Cognito user pool: create pool, app client (no secret for a browser app), 2 users
  - API Gateway HTTP API -> JWT authorizer (issuer = the pool, audience = the app client)
  - Route POST /chat -> Lambda -> AgentCore runtime
  VERIFY: a call with no token returns 401; with a valid token returns 200.
  Driver links: Lambda mints a signed token (driver_id, shipment_id, exp 30 min).
  VERIFY: an expired link returns 401 and the driver sees a plain re-request message.

### B7. Audit trail (20 min)
  decision_audit is a plain Postgres table, append-only:
    REVOKE UPDATE, DELETE ON decision_audit FROM app_user;
  VERIFY: as app_user, an UPDATE fails. That failure IS the audit guarantee - screenshot it.
  Optional later: nightly export to S3 with Object Lock (compliance mode) for immutability.
  PII is never written to an audit row. Hashes and spans only.

### B8. Observability (20 min)
  - LangSmith: set LANGSMITH_API_KEY + project; traces appear per turn
  - CloudWatch: one log group per component, 30-day retention
  VERIFY: run one scenario, then find that same turn in LangSmith AND in decision_audit
  AND in CloudWatch. Three views of one event is the thing to demo.

### B9. Teardown (do not skip)
  Delete the AgentCore runtime, the ECR image, the RDS instance (if created), the NAT
  gateway if any. NAT and RDS are the only things that cost real money when idle.

## PART C — Cost shape (from the Ramco pilot's measured numbers)
  Always-on dominates: RDS and NAT, not models. AgentCore scales to zero.
  For a demo: local Postgres + AgentCore on demand = a few dollars.
  Avoid creating a NAT gateway for a 3-day project. It is the single largest idle cost.

## PART D — What breaks and what to do
  AccessDeniedException on InvokeModel -> model access not enabled, or bare model id
    used where an apac.* inference profile is required (see B1)
  Timeout on first call                -> cold start; retry once, then warm
  ThrottlingException                  -> backoff; this is where p95 latency actually lives
  401 from API Gateway                 -> audience/issuer mismatch in the JWT authorizer
  Cannot reach RDS                     -> security group, not credentials. Check SG first.

---

# Part E — what is actually deployed, and what it does not prove

This was deployed and driven in a browser on 25 Nov 2026.

| Piece | Where |
|---|---|
| Agent + API | AWS Lambda `setuhaul-dock-agent`, ap-southeast-2, python3.12, 1024 MB |
| Public endpoint | Lambda Function URL, auth NONE, CORS owned by the Function URL |
| Model access | Execution role `setuhaul-dock-agent-exec`, scoped to `amazon.nova-*` only |
| Web interface | Vercel, `https://setuhaul-dock-desk.vercel.app` |
| State | SQLite rebuilt into `/tmp` on cold start |

Measured through the public URL: a booking turn **833–986 ms**, an escalation **892 ms**, a refused
injection **1 ms**.

## Three things this deployment is not

**1. It is not access-controlled.** The Function URL is public. A shared token blocks drive-by
scanners, but it ships inside the browser bundle, so anyone who opens the page can read it. That is
a demo gate, not authentication. The real design is the Cognito pool in Part A, where a token is
issued per person and carries a facility.

**2. It is not the integrity guarantee.** Each Lambda container gets its own `/tmp`, so two warm
containers would hold two separate SQLite files and two drivers could be sold the same slot — the
exact failure this project exists to prevent. The first QA pass showed it directly: the header
reported 3 audit rows while the audit table reported none, because the two reads landed on
different containers.

The demo is pinned to **reserved concurrency 1**, which forces every request through one container
and restores single-writer semantics. That makes the demo honest, not the architecture correct.
The guarantee comes from the database, and the proof in `docs/INTEGRITY_PROOF.json` is a
single-writer proof. **On Postgres with `slot_locks` as written, the guarantee holds with no
concurrency limit at all** — that is the migration, and it is the reason the design never leaned on
application-level locking.

**3. State is not durable.** `/tmp` is wiped on cold start, so the day resets by itself. Fine for a
demo; it is the second reason the design calls for Postgres.

## Teardown

```bash
aws lambda delete-function-url-config --function-name setuhaul-dock-agent --region ap-southeast-2
aws lambda delete-function          --function-name setuhaul-dock-agent --region ap-southeast-2
aws iam delete-role-policy --role-name setuhaul-dock-agent-exec --policy-name invoke-nova
aws iam detach-role-policy --role-name setuhaul-dock-agent-exec \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role        --role-name setuhaul-dock-agent-exec
npx vercel remove setuhaul-dock-desk --yes
```

Nothing here costs money at rest except CloudWatch log retention. There is no NAT gateway, no
provisioned concurrency and no idle database — which was deliberate, after the Ramco pilot spent
$43/month on a NAT gateway that served one function.
