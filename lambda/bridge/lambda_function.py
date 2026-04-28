import json
import os
import hashlib
import time
import logging

import boto3
from boto3.dynamodb.conditions import Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AGENT_SPACE_ID = os.environ['AGENT_SPACE_ID']
REGION = os.environ.get('AWS_REGION', 'us-east-1')
DEDUP_TABLE = os.environ.get('DEDUP_TABLE', '')
DEDUP_TTL = int(os.environ.get('DEDUP_TTL_SECONDS', '1800'))

client = boto3.client('devops-agent', region_name=REGION)
ddb = boto3.resource('dynamodb', region_name=REGION).Table(DEDUP_TABLE) if DEDUP_TABLE else None

# Higher number = more severe. Used for upgrade detection.
LEVEL_ORDER = {'warning': 1, 'important': 2, 'critical': 3, 'urgent': 4}

# Map incoming severity (Guance status / inferred from other sources) to
# DevOps Agent Task priority. Unknown severity falls back to HIGH.
SEVERITY_TO_PRIORITY = {
    'urgent': 'CRITICAL',
    'critical': 'CRITICAL',
    'important': 'HIGH',
    'error': 'HIGH',
    'high': 'HIGH',
    'warning': 'MEDIUM',
    'medium': 'MEDIUM',
    'low': 'LOW',
    'info': 'LOW',
}


def _map_priority(severity):
    return SEVERITY_TO_PRIORITY.get((severity or '').lower(), 'HIGH')


def _level_rank(status):
    return LEVEL_ORDER.get(status.lower(), 0)


def _fingerprint(body):
    """Hash of monitor + dimension to identify the same alert instance."""
    monitor_id = body.get('df_monitor_id') or body.get('monitor_id') or ''
    if not monitor_id:
        return None  # Cannot reliably dedup without monitor identity
    dimension = body.get('df_dimension_tags') or body.get('dimension_tags') or ''
    raw = f"{monitor_id}|{dimension}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_duplicate(fingerprint, level):
    """Check dedup table. Returns True if should skip (same or lower level within TTL)."""
    if not ddb:
        return False
    now = int(time.time())
    try:
        # Atomic put: only succeeds if no unexpired record exists, or level upgraded.
        ddb.put_item(
            Item={
                'fingerprint': fingerprint,
                'level': level,
                'expireAt': now + DEDUP_TTL,
            },
            ConditionExpression=(
                Attr('fingerprint').not_exists()
                | Attr('expireAt').lt(now)
                | Attr('level').lt(level)
            ),
        )
        return False  # Put succeeded → not a duplicate, proceed.
    except ddb.meta.client.exceptions.ConditionalCheckFailedException:
        return True  # Record exists, not expired, level not upgraded → duplicate.


def _normalize_event(event):
    """Normalize different event sources into a unified body dict.

    Supported sources:
    - API Gateway (观测云 Webhook): event['body'] contains JSON string
    - SNS (CloudWatch Alarm → SNS → Lambda): event['Records'][0]['Sns']['Message']
    """
    # SNS trigger
    records = event.get('Records', [])
    if records and records[0].get('EventSource') == 'aws:sns':
        msg_str = records[0]['Sns'].get('Message', '{}')
        try:
            msg = json.loads(msg_str)
        except json.JSONDecodeError:
            return {}
        # CloudWatch Alarm message format
        if 'AlarmName' in msg:
            state = msg.get('NewStateValue', '')
            trigger = msg.get('Trigger', {})
            dims = ', '.join(
                f"{d['name']}={d['value']}" for d in trigger.get('Dimensions', [])
            )
            status_map = {'ALARM': 'critical', 'INSUFFICIENT_DATA': 'nodata', 'OK': 'ok'}
            return {
                'title': msg['AlarmName'],
                'status': status_map.get(state, state.lower()),
                'message': msg.get('NewStateReason', ''),
                'monitor_id': msg.get('AlarmArn', msg['AlarmName']),
                'dimension_tags': dims,
            }
        # Generic SNS JSON payload — pass through
        return msg

    # API Gateway trigger (观测云 Webhook)
    body_str = event.get('body', '{}')
    if isinstance(body_str, str):
        try:
            return json.loads(body_str)
        except json.JSONDecodeError:
            return {}
    return body_str if isinstance(body_str, dict) else {}


def handler(event, context):
    logger.info(f"Raw event: {json.dumps(event)[:2000]}")

    body = _normalize_event(event)

    title = (body.get('df_title') or body.get('title')
             or body.get('Result', {}).get('title') or 'Guance Alert')
    status = body.get('df_status') or body.get('status') or ''
    message = body.get('df_message') or body.get('message') or json.dumps(body)[:500]
    dimension = body.get('df_dimension_tags') or body.get('dimension_tags') or ''

    if not status or status in ('ok', 'nodata', 'info'):
        logger.info(f"Skip: status={status}")
        return {'statusCode': 200, 'body': json.dumps({'skip': status})}

    # Dedup check
    fp = _fingerprint(body)
    level = _level_rank(status)
    if fp and _is_duplicate(fp, level):
        logger.info(f"Dedup skip: fingerprint={fp} status={status}")
        return {'statusCode': 200, 'body': json.dumps({'skip': 'duplicate', 'fingerprint': fp})}

    description = (
        f"观测云告警自动触发调查。\n\n"
        f"告警: {title}\n"
        f"级别: {status}\n"
        f"维度: {dimension}\n"
        f"详情: {message}\n\n"
        f"请通过观测云 MCP 执行以下分析:\n"
        f"1. 查询最近 15 分钟的错误链路: T::*:(*) {{ status = 'error' }} [15m]\n"
        f"2. 查询相关服务的错误日志: L::*:(*) {{ level = 'error' }} [15m]\n"
        f"3. 检查基础设施指标: M::cpu:(avg(usage_total)) BY host\n"
        f"4. 给出根因分析和修复建议"
    )

    priority = _map_priority(status)
    resp = client.create_backlog_task(
        agentSpaceId=AGENT_SPACE_ID,
        taskType='INVESTIGATION',
        title=f'[Auto] {title}'[:128],
        description=description,
        priority=priority
    )

    task = resp.get('task', {})
    result = {
        'taskId': task.get('taskId'),
        'executionId': task.get('executionId'),
        'status': task.get('status')
    }
    logger.info(f"Investigation created: {json.dumps(result)} fingerprint={fp}")
    return {'statusCode': 200, 'body': json.dumps(result)}
