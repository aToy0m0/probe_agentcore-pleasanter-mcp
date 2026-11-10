const { App, Stack } = require("aws-cdk-lib");
const { CfnInclude } = require("aws-cdk-lib/cloudformation-include");
const { execFileSync } = require("node:child_process");
const { join } = require("node:path");
const { writeFileSync } = require("node:fs");

const app = new App();
const registration = app.node.tryGetContext("registration") === "true";
const sourceDirectory = registration ? join(__dirname, "registration") : __dirname;
const templateFile = join(sourceDirectory, "template.json");
writeFileSync(templateFile, execFileSync("python", [join(sourceDirectory, "build.py")]));
const stackName = registration ? "agentcore-mcp-key-registration" : "agentcore-cognito-mcp-probe";
const stack = new Stack(app, stackName, {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: "us-east-1" },
});
new CfnInclude(stack, registration ? "Registration" : "Probe", { templateFile });
