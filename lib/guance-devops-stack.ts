import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'path';
import * as fs from 'fs';
import { execSync } from 'child_process';

export interface GuanceDevopsConfig {
  agentSpaceId: string;
  region?: string;
  feishuWebhookUrl?: string;
  wechatWebhookUrl?: string;
  apiKey: string;
  tags?: Record<string, string>;
}

interface GuanceDevopsStackProps extends cdk.StackProps {
  config: GuanceDevopsConfig;
}

export class GuanceDevopsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GuanceDevopsStackProps) {
    super(scope, id, props);

    const { config } = props;
    if (!config.apiKey) {
      throw new Error('config.apiKey is required. Run "npm run setup" to generate one.');
    }
    const spaceArn = `arn:aws:aidevops:${this.region}:${this.account}:agentspace/${config.agentSpaceId}`;

    if (config.tags) {
      for (const [key, value] of Object.entries(config.tags)) {
        cdk.Tags.of(this).add(key, value);
      }
    }

    // Cross-platform Python bundling: try local pip, fall back to Docker.
    const pythonBundling = (codePath: string): lambda.Code =>
      lambda.Code.fromAsset(codePath, {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -r . /asset-output',
          ],
          local: {
            tryBundle(outputDir: string) {
              const pip = ['pip3', 'pip'].find(p => {
                try { execSync(`${p} --version`, { stdio: 'ignore' }); return true; } catch { return false; }
              });
              if (!pip) return false;
              try {
                fs.mkdirSync(outputDir, { recursive: true });
                execSync(`${pip} install -r requirements.txt -t "${outputDir}"`,
                  { cwd: codePath, stdio: 'inherit' });
                fs.cpSync(codePath, outputDir, { recursive: true });
                return true;
              } catch (e) {
                console.error(`[bundling] local failed, falling back to Docker: ${e}`);
                return false;
              }
            },
          },
        },
      });

    // ==================== Bridge Lambda ====================
    const bridgeFn = new lambda.Function(this, 'BridgeFn', {
      functionName: 'guance-devops-bridge',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.handler',
      code: pythonBundling(path.join(__dirname, '..', 'lambda', 'bridge')),
      timeout: cdk.Duration.seconds(30),
      environment: { AGENT_SPACE_ID: config.agentSpaceId },
    });

    bridgeFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aidevops:CreateBacklogTask'],
      resources: [spaceArn],
    }));

    // ==================== API Key Authorizer Lambda ====================
    const authorizerFn = new lambda.Function(this, 'AuthorizerFn', {
      functionName: 'guance-webhook-authorizer',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromInline(`
import os
def handler(event, context):
    expected = os.environ['API_KEY']
    headers = event.get('headers', {})
    token = headers.get('x-api-key', '')
    if token == expected:
        return {"isAuthorized": True}
    return {"isAuthorized": False}
`),
      timeout: cdk.Duration.seconds(5),
      environment: { API_KEY: config.apiKey },
    });

    const httpAuthorizer = new authorizers.HttpLambdaAuthorizer('ApiKeyAuth', authorizerFn, {
      responseTypes: [authorizers.HttpLambdaResponseType.SIMPLE],
      identitySource: ['$request.header.x-api-key'],
    });

    // ==================== API Gateway HTTP API ====================
    const httpApi = new apigatewayv2.HttpApi(this, 'WebhookApi', {
      apiName: 'guance-webhook-api',
    });

    httpApi.addRoutes({
      path: '/webhook',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: new integrations.HttpLambdaIntegration('BridgeIntegration', bridgeFn),
      authorizer: httpAuthorizer,
    });

    // ==================== Notify Lambda ====================
    const notifyEnv: Record<string, string> = {};
    if (config.feishuWebhookUrl) notifyEnv['FEISHU_WEBHOOK_URL'] = config.feishuWebhookUrl;
    if (config.wechatWebhookUrl) notifyEnv['WECHAT_WEBHOOK_URL'] = config.wechatWebhookUrl;

    const notifyFn = new lambda.Function(this, 'NotifyFn', {
      functionName: 'devops-agent-notify',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      code: pythonBundling(path.join(__dirname, '..', 'lambda', 'notify')),
      timeout: cdk.Duration.seconds(60),
      environment: notifyEnv,
    });

    notifyFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aidevops:GetBacklogTask', 'aidevops:ListJournalRecords'],
      resources: [spaceArn],
    }));

    // ==================== EventBridge Rule + CloudWatch Logs ====================
    const logGroup = new logs.LogGroup(this, 'InvestigationLogs', {
      logGroupName: '/aws/events/devops-agent-investigations',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const rule = new events.Rule(this, 'InvestigationRule', {
      ruleName: 'devops-agent-investigation-done',
      eventPattern: {
        source: ['aws.aidevops'],
        detailType: [
          'Investigation Completed',
          'Investigation Failed',
          'Investigation Timed Out',
        ],
        detail: {
          metadata: { agent_space_id: [config.agentSpaceId] },
        },
      },
    });

    rule.addTarget(new targets.CloudWatchLogGroup(logGroup));
    rule.addTarget(new targets.LambdaFunction(notifyFn));

    // ==================== Outputs ====================
    const webhookUrl = `${httpApi.apiEndpoint}/webhook`;

    new cdk.CfnOutput(this, 'WebhookUrl', {
      value: webhookUrl,
      description: '观测云 Webhook 通知对象地址',
    });

    new cdk.CfnOutput(this, 'ApiKey', {
      value: config.apiKey,
      description: '配置到观测云 Webhook Header: x-api-key',
    });

    new cdk.CfnOutput(this, 'NextStep', {
      value: `在观测云创建 Webhook 通知对象，地址填 ${webhookUrl}，Header 添加 x-api-key: ${config.apiKey}`,
      description: '部署后操作指引',
    });
  }
}
