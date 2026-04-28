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
if (config.agentSpaceId === 'YOUR_AGENT_SPACE_ID') config.agentSpaceId = '';

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
