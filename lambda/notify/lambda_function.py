import json
import os
import urllib.request

import boto3

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK_URL", "")
WECHAT_WEBHOOK = os.environ.get("WECHAT_WEBHOOK_URL", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
client = boto3.client('devops-agent', region_name=REGION)


def _post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8", errors="replace")


def lambda_handler(event, context):
    detail = event.get("detail", {})
    detail_type = event.get("detail-type", "Unknown")
    meta = detail.get("metadata", {})
    data = detail.get("data", {})

    space_id = meta.get("agent_space_id", "")
    task_id = meta.get("task_id", "")
    execution_id = meta.get("execution_id", "")
    status = data.get("status", detail_type)

    title = "N/A"
    if space_id and task_id:
        try:
            resp = client.get_backlog_task(agentSpaceId=space_id, taskId=task_id)
            title = resp.get("task", {}).get("title", "N/A")
        except Exception as e:
            print(f"ERROR get_backlog_task: {e}")
            title = f"(error: {e})"

    symptoms_text = "N/A"
    findings_text = ""
    mitigation_lines = []

    if space_id and execution_id:
        try:
            jr = client.list_journal_records(agentSpaceId=space_id, executionId=execution_id)
            for rec in jr.get("records", []):
                if rec.get("recordType") == "investigation_summary":
                    summary = json.loads(rec["content"])
                    syms = summary.get("symptoms", [])
                    if syms:
                        symptoms_text = "\n".join([f"• {s.get('title', '')}" for s in syms])
                    for f in summary.get("findings", []):
                        ftype = f.get("type", "").upper()
                        ftitle = f.get("title", "")
                        fdesc = f.get("description", "")[:600]
                        findings_text += f"\n**[{ftype}]** {ftitle}\n{fdesc}\n"
                        if ftype == "ROOT_CAUSE":
                            mitigation_lines.append(f"🔴 {ftitle}")
                        else:
                            mitigation_lines.append(f"🟡 {ftitle}")
                    break
        except Exception as e:
            print(f"ERROR list_journal_records: {e}")
            findings_text = f"\n(error fetching details: {e})"

    mitigation_text = "\n".join(mitigation_lines) if mitigation_lines else "N/A"

    if FEISHU_WEBHOOK:
        send_feishu(detail_type, status, title, task_id, symptoms_text, findings_text, mitigation_text)
    if WECHAT_WEBHOOK:
        send_wechat(detail_type, status, title, task_id, symptoms_text, findings_text, mitigation_text)

    return {"statusCode": 200}


def send_feishu(detail_type, status, title, task_id, symptoms, findings, mitigation):
    color = "green" if "Completed" in detail_type else "red"
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🔍 DevOps Agent: {status}"},
                "template": color
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**Title:** {title}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**Task ID:** {task_id}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**📋 Symptoms:**\n{symptoms}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**🔎 Root Cause & Findings:**{findings[:3000]}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**🛠 Mitigation Plan:**\n{mitigation}"}},
            ]
        }
    }
    try:
        print(f"Feishu response: {_post_json(FEISHU_WEBHOOK, card)}")
    except Exception as e:
        print(f"ERROR send_feishu: {e}")


def send_wechat(detail_type, status, title, task_id, symptoms, findings, mitigation):
    header = '<font color="info">**🔍 调查完成**</font>' if "Completed" in detail_type \
        else '<font color="warning">**🔍 调查异常**</font>'
    content = f"""{header}
> 标题:<font color="comment">{title}</font>
> 状态:<font color="comment">{status}</font>
> Task ID:<font color="comment">{task_id}</font>

**📋 Symptoms:**
{symptoms}

**🔎 Root Cause & Findings:**
{findings[:2000]}

**🛠 Mitigation Plan:**
{mitigation}"""
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        print(f"WeChat response: {_post_json(WECHAT_WEBHOOK, payload)}")
    except Exception as e:
        print(f"ERROR send_wechat: {e}")
