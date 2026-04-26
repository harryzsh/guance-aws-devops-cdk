import json
import os
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AGENT_SPACE_ID = os.environ['AGENT_SPACE_ID']
REGION = os.environ.get('AWS_REGION', 'us-east-1')
client = boto3.client('devops-agent', region_name=REGION)


def handler(event, context):
    logger.info(f"Raw event: {json.dumps(event)[:2000]}")

    body_str = event.get('body', '{}')
    if isinstance(body_str, str):
        try:
            body = json.loads(body_str)
        except json.JSONDecodeError:
            body = {}
    else:
        body = body_str

    title = (body.get('df_title') or body.get('title')
             or body.get('Result', {}).get('title') or 'Guance Alert')
    status = body.get('df_status') or body.get('status') or ''
    message = body.get('df_message') or body.get('message') or json.dumps(body)[:500]
    dimension = body.get('df_dimension_tags') or body.get('dimension_tags') or ''

    if status in ('ok', 'nodata', 'info'):
        logger.info(f"Skip: status={status}")
        return {'statusCode': 200, 'body': json.dumps({'skip': status})}

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

    resp = client.create_backlog_task(
        agentSpaceId=AGENT_SPACE_ID,
        taskType='INVESTIGATION',
        title=f'[Auto] {title}'[:128],
        description=description,
        priority='HIGH'
    )

    task = resp.get('task', {})
    result = {
        'taskId': task.get('taskId'),
        'executionId': task.get('executionId'),
        'status': task.get('status')
    }
    logger.info(f"Investigation created: {json.dumps(result)}")
    return {'statusCode': 200, 'body': json.dumps(result)}
