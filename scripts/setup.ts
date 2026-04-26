import prompts from 'prompts';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';

async function main() {
  console.log('\n🔧 观测云 × AWS DevOps Agent CDK 配置向导\n');

  const response = await prompts([
    {
      type: 'text',
      name: 'agentSpaceId',
      message: 'AWS DevOps Agent Space ID',
      validate: (v: string) => v.length > 0 || 'Required',
    },
    {
      type: 'text',
      name: 'region',
      message: 'AWS Region',
      initial: 'us-east-1',
    },
    {
      type: 'multiselect',
      name: 'channels',
      message: '通知渠道 (空格选择，回车确认)',
      choices: [
        { title: '飞书 (Feishu/Lark)', value: 'feishu' },
        { title: '企业微信 (WeCom)', value: 'wechat' },
      ],
    },
  ]);

  if (!response.agentSpaceId) {
    console.log('❌ 已取消');
    process.exit(1);
  }

  let feishuWebhookUrl = '';
  let wechatWebhookUrl = '';

  if (response.channels?.includes('feishu')) {
    const r = await prompts({
      type: 'text',
      name: 'url',
      message: '飞书 Webhook URL',
      validate: (v: string) => v.startsWith('https://') || '请输入 https:// 开头的 URL',
    });
    feishuWebhookUrl = r.url || '';
  }

  if (response.channels?.includes('wechat')) {
    const r = await prompts({
      type: 'text',
      name: 'url',
      message: '企业微信 Webhook URL',
      validate: (v: string) => v.startsWith('https://') || '请输入 https:// 开头的 URL',
    });
    wechatWebhookUrl = r.url || '';
  }

  const apiKeyResp = await prompts({
    type: 'text',
    name: 'apiKey',
    message: 'API Key (留空自动生成)',
    initial: '',
  });

  const apiKey = apiKeyResp.apiKey || crypto.randomUUID();

  // Tags
  const tags: Record<string, string> = {};
  const tagResp = await prompts({
    type: 'confirm',
    name: 'addTags',
    message: '是否添加自定义 Tags？',
    initial: false,
  });
  if (tagResp.addTags) {
    console.log('  输入 Tag（key=value），留空结束：');
    while (true) {
      const t = await prompts({ type: 'text', name: 'tag', message: 'Tag (key=value)' });
      if (!t.tag) break;
      const [k, ...v] = t.tag.split('=');
      if (k && v.length) tags[k.trim()] = v.join('=').trim();
    }
  }

  const config = {
    agentSpaceId: response.agentSpaceId,
    region: response.region || 'us-east-1',
    feishuWebhookUrl,
    wechatWebhookUrl,
    apiKey,
    tags,
  };

  const configPath = path.join(__dirname, '..', 'config.json');
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n');
  console.log(`\n✅ 配置已保存到 config.json`);
  console.log(`   API Key: ${apiKey}`);
  console.log(`\n下一步: npx cdk deploy\n`);
}

main().catch(console.error);
