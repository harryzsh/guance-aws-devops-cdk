# Connecting the Guance MCP Server to AWS DevOps Agent (optional)

This guide walks through connecting the hosted **Guance MCP Server** to your AWS DevOps Agent Space so the agent can, during an investigation, directly pull data from Guance (logs, metrics, traces, RUM, dashboards, monitors).

It is an **optional post-deploy step** — the CDK stack in this repo (`GuanceDevopsStack`) does not register MCP servers for you, and the deployed alerting/notification pipeline works fine without it. Follow this doc only if you also want the agent to **query Guance data** while investigating.

> **Credential safety**: this document contains only placeholders. Anywhere you see `{...}` or `your-...`, replace the value locally. Do **not** commit your Guance API key or paste it into screenshots / chat transcripts.

---

## When does this make sense?

Connect Guance MCP when you want the agent to do things like:

- On an alert, correlate the triggering monitor with recent error logs in Guance (`query_log_data`).
- Pull the last-15-minute service latency / error rate from APM while triaging (`query_trace_data`, `query_metric_data`).
- Inspect RUM page-level performance for frontend incidents (`query_rum_data`).
- List / look up the monitor, dashboard, or log data access rule that fired (`list_checkers`, `list_dashboards`, `list_logging_query_rules`).

If you only need the one-way flow "Guance alert → DevOps Agent creates investigation task → Feishu/WeCom notification", you do **not** need this integration.

---

## 1. Prerequisites

| Item | Required | Notes |
|---|---|---|
| A working `GuanceDevopsStack` | ✅ | Deployed per [`deployment-log.md`](./deployment-log.md). Your Agent Space must already exist. |
| Guance commercial plan | ✅ | The hosted MCP endpoint `obsy-ai.guance.com` is a commercial-plan feature. |
| Guance workspace API key | ✅ | Create in **Management → API Keys Management**. The key inherits the workspace's data access scope — create a dedicated read-oriented key for the agent. |
| Guance `SITE_KEY` | ✅ | The site code of your workspace, e.g. `cn1`, `us1`, `ap1`. See the full map below. |
| AWS region that supports DevOps Agent | ✅ | `us-east-1`, `us-west-2`, `ap-southeast-2`, `ap-northeast-1`, `eu-central-1`, `eu-west-1`. |
| IAM permission to register MCP and associate it with an Agent Space | ✅ | Typically the same principal you used for `cdk deploy`. |

### Guance `SITE_KEY` map

Pick the one matching your Guance tenant:

| SITE_KEY | Region (Guance) | Openapi base |
|---|---|---|
| `cn1` | China 1 (Hangzhou) — default | `https://openapi.guance.com` |
| `cn2` | China 2 (Ningxia) | `https://aws-openapi.guance.com` |
| `cn4` | China 4 (Guangzhou) | `https://cn4-openapi.guance.com` |
| `cn6` | China 6 (Hong Kong) | `https://cn6-openapi.guance.one` |
| `us1` | Overseas 1 (Oregon) | `https://us1-openapi.guance.com` |
| `eu1` | Europe 1 (Frankfurt) | `https://eu1-openapi.guance.one` |
| `ap1` | APAC 1 (Singapore) | `https://ap1-openapi.guance.one` |
| `za1` | Africa 1 (South Africa) | `https://za1-openapi.guance.com` |
| `id1` | Indonesia 1 (Jakarta) | `https://id1-openapi.guance.com` |

> The `SITE_KEY` you pick must match the workspace that owns the API key. If they don't match, the MCP server will reject the request.

### The Guance MCP endpoint and auth header

Both are **fixed** by Guance — you do not construct them yourself:

- **Endpoint**: `https://obsy-ai.guance.com/obsy_ai_mcp/mcp`
- **HTTP header name**: `Authorization`
- **HTTP header value**: `{YOUR_GUANCE_API_KEY};Endpoint={YOUR_SITE_KEY}`
  - Example: `tkn_abcd1234efgh5678;Endpoint=cn1`
  - Note the literal `;Endpoint=` in the middle — this is **not** a standard `Bearer` token, it's a single header value that Guance's MCP server parses itself.

Because the value is one single string in one header, the matching DevOps Agent auth type is **API Key** (not Bearer). See §2 below.

---

## 2. Register the Guance MCP server in DevOps Agent

MCP servers are registered **at the AWS account level** and can then be attached to individual Agent Spaces. This is a one-time per account step.

### 2a. AWS Console — the recommended path

1. Sign in to the AWS Management Console, switch to the region where your `GuanceDevopsStack` is deployed.
2. Open **AWS DevOps Agent** → **Capability Providers** (left nav).
3. Under **Available** providers, find **MCP Server** → click **Register**.
4. Fill in the **MCP server details** page:
   - **Name**: `guance` (lowercase, no spaces; this is the registration name inside AWS, not a display label in Guance).
   - **Endpoint URL**: `https://obsy-ai.guance.com/obsy_ai_mcp/mcp`
   - **Description** *(optional)*: `Guance hosted MCP — logs/metrics/traces/RUM/monitors via DQL`.
   - **Enable Dynamic Client Registration**: leave **unchecked**. Guance uses a static API key, not OAuth DCR.
   - Click **Next**.
5. On **Authorization flow**, pick **API Key**. Click **Next**.
6. On **Authorization configuration**:
   - **API key name**: any friendly label, e.g. `guance-workspace`.
   - **Header name**: `Authorization` — **literal string, not a Bearer token header**.
   - **API key value**: your composite string, exactly:

     ```
     {YOUR_GUANCE_API_KEY};Endpoint={YOUR_SITE_KEY}
     ```

     For example, with a `cn1` workspace and a key `tkn_abcd1234efgh5678`:

     ```
     tkn_abcd1234efgh5678;Endpoint=cn1
     ```

   - Click **Next**.
7. Review and **Submit**. AWS will validate the connection against the Guance endpoint. On success, the registration shows up as active and is visible to all Agent Spaces in the account.

### 2b. Verify from the AWS CLI

Run this to confirm the registration lined up the way the rest of this doc assumes:

```bash
aws devops-agent list-services --region {your-region} --output json
```

You should see an entry similar to:

```json
{
  "serviceId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "serviceType": "mcpserver",
  "additionalServiceDetails": {
    "mcpserver": {
      "name": "guance",
      "endpoint": "https://obsy-ai.guance.com/obsy_ai_mcp/mcp",
      "authorizationMethod": "api-key",
      "apiKeyHeader": "Authorization"
    }
  }
}
```

Record the `serviceId` — you'll need it when associating with a Space via CLI (§3b).

> The API key value is **not** returned in `list-services` / `get-service` output. If you need to rotate it, **deregister and re-register** — there is no in-place update.

---

## 3. Attach Guance MCP to your Agent Space

Registration alone doesn't expose tools to an agent. You need to explicitly **associate** the registered MCP server with each Agent Space that should use it, and — importantly — **allowlist the specific tools**.

### 3a. AWS Console — recommended

1. In the AWS DevOps Agent console, open the Agent Space that matches the one your CDK stack created or referenced (see the `AgentSpaceId` CloudFormation output of `GuanceDevopsStack`).
2. Go to the **Capabilities** tab.
3. In the **MCP Servers** section, click **Add**.
4. Pick the `guance` registration.
5. Choose **Select specific tools** (avoid "Allow all tools" — see the security note below), then allowlist:

   | Tool | Purpose |
   |---|---|
   | `list_checkers` | List Guance monitors (first 10 by default). |
   | `list_dashboards` | List dashboards (first 10 by default). |
   | `list_logging_query_rules` | List log data access rules. |
   | `query_log_data` | Run a DQL log query. Namespace `L`. |
   | `query_metric_data` | Run a DQL metric query. Namespace `M`. |
   | `query_trace_data` | Run a DQL trace/APM query. Namespace `T`. |
   | `query_rum_data` | Run a DQL RUM query. Namespace `R`. |

6. Click **Add**.

All seven above are **read-only** queries against DQL. That matches AWS's guidance to only allowlist read-only MCP tools and to scope credentials to read-only access.

### 3b. AWS CLI — if you prefer scripting

Using the `serviceId` from §2b and your Space's ID:

```bash
AGENT_SPACE_ID='{your-agent-space-id}'   # from CFN output AgentSpaceId
SERVICE_ID='{serviceId-from-list-services}'

aws devops-agent associate-service \
  --region {your-region} \
  --agent-space-id "$AGENT_SPACE_ID" \
  --service-id "$SERVICE_ID" \
  --configuration '{
    "mcpserver": {
      "tools": [
        "list_checkers",
        "list_logging_query_rules",
        "list_dashboards",
        "query_log_data",
        "query_metric_data",
        "query_trace_data",
        "query_rum_data"
      ]
    }
  }'

# Confirm it's attached
aws devops-agent list-associations \
  --region {your-region} \
  --agent-space-id "$AGENT_SPACE_ID"
```

You're looking for an association whose `configuration.mcpserver.tools` contains the seven tool names and whose `serviceId` matches the Guance registration.

---

## 4. Smoke test

### 4a. Sanity-check the Guance credential directly (no AWS involved)

Before blaming DevOps Agent, verify the composite header string works end-to-end against Guance. From your workstation:

```bash
GUANCE_API_KEY='{your-guance-api-key}'
SITE_KEY='{your-site-key}'   # e.g. cn1, us1, ap1

curl -i -X POST 'https://obsy-ai.guance.com/obsy_ai_mcp/mcp' \
  -H "Authorization: ${GUANCE_API_KEY};Endpoint=${SITE_KEY}" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expected: an HTTP 200 with a JSON-RPC payload listing tools similar to `list_checkers`, `query_log_data`, etc.

Common failure signals:

- `401` or an auth-error JSON body → the header value is wrong. Double-check the format: **no leading `Bearer `, no space before the semicolon**. The value must match `{key};Endpoint={site}` exactly.
- `403` / "site mismatch" → your `SITE_KEY` doesn't correspond to the workspace the API key belongs to.
- Hang with no response → the commercial MCP feature may not be enabled for your plan; contact Guance support.

After you're done, clear the env vars:

```bash
unset GUANCE_API_KEY SITE_KEY
```

### 4b. Ask the agent to use a Guance tool

From the DevOps Agent chat in the console (inside the Space you attached Guance MCP to), start a chat and try one of:

> "Using the Guance MCP server, list my monitors."
>
> "Using the Guance MCP server, query the last 5 minutes of error logs for service `my-service`."

The agent should call `list_checkers` or `query_log_data` and return structured results. If it replies that it has no matching tool, re-check the association in §3 — the tool allowlist is per-Space.

### 4c. End-to-end through a real alert

Once the per-Space association looks good:

1. Trigger a low-severity Guance monitor that your `GuanceDevopsStack` already knows how to route (see the README). The CDK-deployed Bridge Lambda creates a DevOps Agent investigation task.
2. Open the task in the DevOps Agent console.
3. In the agent's action log, confirm it calls one or more `query_*` tools from the `guance` MCP server while investigating.
4. The completion event still flows through EventBridge → the `devops-agent-notify` Lambda → your Feishu / WeCom webhook. The Guance integration is purely additive; it doesn't alter that path.

---

## 5. Security considerations (read this before going to production)

- **Dedicated read-only API key**: generate an API key in Guance that's used *only* by DevOps Agent. Don't reuse your personal key. If Guance supports scoping the key to read-only / specific workspaces in your plan, do so.
- **Tool allowlist over "allow all"**: the seven tools listed in §3 are the confirmed read-only query set. Avoid enabling any future Guance MCP tool that writes (creates monitors, modifies dashboards, etc.) unless you've reviewed the blast radius.
- **Prompt injection exposure**: when the agent reads log/trace content via `query_log_data` / `query_trace_data`, attacker-controlled log lines can try to inject instructions ("ignore previous instructions…"). AWS DevOps Agent has built-in prompt-injection protection, but this is still a shared-responsibility boundary — don't give the agent credentials that can mutate production.
- **Endpoint is logged in CloudTrail**: per AWS docs, the MCP endpoint URL you register shows up in CloudTrail. The endpoint is public Guance documentation, so this is fine; just be aware.
- **The key is stored by AWS**: once registered, the key lives in AWS's managed store for DevOps Agent. If you need to rotate it, **deregister and re-register** — there is no in-place update API.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Console "Register" fails at validation step | Header name/value wrong, or Guance unreachable from your AWS region | Re-run the curl smoke test in §4a. Fix the header format or SITE_KEY first, then retry registration. |
| Association succeeds, but the agent "can't find any Guance tools" | Tool allowlist empty or wrong name | `aws devops-agent list-associations ...` — confirm `configuration.mcpserver.tools` contains the seven names exactly. Tool names are case-sensitive. |
| Agent returns 401/403 when calling a tool | API key rotated or revoked on Guance side | In Guance, confirm the key is still active. Then deregister + re-register in DevOps Agent (§2). There is no in-place update. |
| `query_log_data` returns no rows even though Guance UI shows rows | DQL defaults — time window is last 5 min, namespace is `L`, datasource is `default` | Be explicit in the prompt: time range, `namespace`, `datasource`, `select-clause`, filters. See the Guance MCP docs' DQL examples. |
| Response is very large / truncated | The tool returns many rows | Ask the agent to narrow the DQL: add `where-clause` filters, use `count(*)` or `last(*)`, or shrink the time window. |
| Agent uses Guance in one Space but not another | Association is **per Space**, not per account | Repeat §3 for each Space that needs the integration. |

---

## 7. Cleanup

If you want to remove the integration completely:

```bash
AGENT_SPACE_ID='{your-agent-space-id}'
ASSOCIATION_ID='{association-id-from-list-associations}'
SERVICE_ID='{service-id-of-the-guance-registration}'

# 1. Detach from each Agent Space
aws devops-agent disassociate-service \
  --region {your-region} \
  --agent-space-id "$AGENT_SPACE_ID" \
  --association-id "$ASSOCIATION_ID"

# 2. Once no Space uses it, deregister the account-level MCP server
aws devops-agent deregister-service \
  --region {your-region} \
  --service-id "$SERVICE_ID"
```

> Order matters: deregister fails while any Space is still associated with it. Detach first, then deregister.

Removing the Guance MCP integration does **not** affect anything in `GuanceDevopsStack` (webhook, Bridge Lambda, dedup table, Notify Lambda). `cdk destroy` of the stack is independent.

---

## Appendix — References

- AWS DevOps Agent — [Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-mcp-servers.html)
- AWS DevOps Agent API — [`RegisteredMCPServerDetails`](https://docs.aws.amazon.com/devopsagent/latest/APIReference/API_RegisteredMCPServerDetails.html)
- Guance — [MCP Server docs](https://docs.guance.com/en/mcp-server/) (endpoint, header format, SITE_KEY map, tool list)
- Guance — [API Keys Management](https://docs.guance.com/en/management/api-key/)
