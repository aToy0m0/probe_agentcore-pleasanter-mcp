const { App, Stack } = require("aws-cdk-lib");
const { CfnInclude } = require("aws-cdk-lib/cloudformation-include");
const { execFileSync } = require("node:child_process");
const { join } = require("node:path");
const { writeFileSync } = require("node:fs");

const templateFile = join(__dirname, "template.json");
writeFileSync(templateFile, execFileSync("python", [join(__dirname, "build.py")]));
const app = new App();
const stack = new Stack(app, "agentcore-cognito-mcp-probe", {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: "us-east-1" },
});
new CfnInclude(stack, "Probe", { templateFile });
