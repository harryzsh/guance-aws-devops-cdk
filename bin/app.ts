#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { GuanceDevopsStack } from '../lib/guance-devops-stack';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';

const configPath = path.join(__dirname, '..', 'config.json');
if (!fs.existsSync(configPath)) {
  console.error('❌ config.json not found. Run "npm run setup" or copy config.example.json to config.json');
  process.exit(1);
}

const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
// Allow empty agentSpaceId to trigger the stack's auto-create path (creates
// an Agent Space named "guance-devops-agent"). Only reject the placeholder.
if (config.agentSpaceId === 'YOUR_AGENT_SPACE_ID') {
  console.error('❌ agentSpaceId still has placeholder value in config.json.');
  console.error('   Set it to an existing Agent Space ID, or to "" to auto-create one.');
  process.exit(1);
}

// Persist an apiKey if missing, so it stays stable across synth/deploy.
if (!config.apiKey) {
  config.apiKey = crypto.randomUUID();
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n');
  console.log(`✨ Generated apiKey and saved to config.json: ${config.apiKey}`);
}

const app = new cdk.App();
new GuanceDevopsStack(app, 'GuanceDevopsStack', {
  env: { region: config.region || 'us-east-1' },
  config,
});
