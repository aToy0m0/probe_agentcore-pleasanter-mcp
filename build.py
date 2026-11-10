"""Generate one CloudFormation template with inline Lambda code."""

import hashlib
import json
import os
from pathlib import Path


def ref(name):
    return {"Ref": name}


def attr(name, key="Arn"):
    return {"Fn::GetAtt": [name, key]}


def sub(value):
    return {"Fn::Sub": value}


def allow(action, resource, **extra):
    return {"Effect": "Allow", "Action": action, "Resource": resource, **extra}


resources = {}


def resource(name, kind, **props):
    resources[name] = {"Type": "AWS::" + kind, "Properties": props}


def role(name, service, permissions, condition=None):
    trust = {"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"Service": service}}
    if condition:
        trust["Condition"] = condition
    resource(name, "IAM::Role", AssumeRolePolicyDocument={"Version": "2012-10-17", "Statement": [trust]},
             Policies=[{"PolicyName": "probe-only", "PolicyDocument": {
                 "Version": "2012-10-17", "Statement": permissions}}])


resource("Pool", "Cognito::UserPool", UserPoolName=ref("AWS::StackName"),
         AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
         Policies={"PasswordPolicy": {"MinimumLength": 16, "RequireUppercase": True,
                                     "RequireLowercase": True, "RequireNumbers": True,
                                     "RequireSymbols": True}},
         UserPoolTier="LITE")
resource("Client", "Cognito::UserPoolClient", UserPoolId=ref("Pool"), GenerateSecret=False,
         ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
         AllowedOAuthFlowsUserPoolClient=True, AllowedOAuthFlows=["code"],
         AllowedOAuthScopes=["openid", "aws.cognito.signin.user.admin"],
         SupportedIdentityProviders=["COGNITO"],
         CallbackURLs=[os.environ.get("PROBE_CALLBACK_URL", "http://127.0.0.1:38500/callback")],
         PreventUserExistenceErrors="ENABLED", EnableTokenRevocation=True,
         AccessTokenValidity=15, TokenValidityUnits={"AccessToken": "minutes"})
resource("LoginDomain", "Cognito::UserPoolDomain", UserPoolId=ref("Pool"),
         Domain=sub("agentcore-mcp-probe-${AWS::AccountId}"))

for logical, username in (("Owner", "owner"), ("Codex", "codex")):
    resource(logical + "User", "Cognito::UserPoolUser", UserPoolId=ref("Pool"),
             Username=username, MessageAction="SUPPRESS")
    resource(logical + "Secret", "SecretsManager::Secret",
             GenerateSecretString={"SecretStringTemplate": "{}", "GenerateStringKey": "apiKey",
                                   "PasswordLength": 40, "ExcludePunctuation": True})
    resources[logical + "Secret"].update(DeletionPolicy="Delete", UpdateReplacePolicy="Delete")
    resource(logical + "LoginSecret", "SecretsManager::Secret",
             GenerateSecretString={"SecretStringTemplate": "{}", "GenerateStringKey": "password",
                                   "PasswordLength": 32, "RequireEachIncludedType": True})
    resources[logical + "LoginSecret"].update(DeletionPolicy="Delete", UpdateReplacePolicy="Delete")

resource("SigningKey", "KMS::Key", KeySpec="RSA_2048", KeyUsage="SIGN_VERIFY",
         PendingWindowInDays=7, KeyPolicy={"Version": "2012-10-17", "Statement": [{
             "Effect": "Allow", "Principal": {"AWS": sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:root")},
             "Action": "kms:*", "Resource": "*"}]})
resources["SigningKey"].update(DeletionPolicy="Delete", UpdateReplacePolicy="Delete")

issuer = sub("https://cognito-idp.${AWS::Region}.${AWS::URLSuffix}/${Pool}")
secrets_env = {"OWNER_SECRET": ref("OwnerSecret"), "CODEX_SECRET": ref("CodexSecret")}
secrets_read = allow("secretsmanager:GetSecretValue", list(secrets_env.values()))
code = Path(__file__).with_name("handlers.py").read_text(encoding="utf-8")
verifier_code = Path(__file__).with_name("verify.py").read_text(encoding="utf-8")

for name, handler, environment, permissions in [
    ("Interceptor", "intercept", {"ISSUER": issuer, "CLIENT_ID": ref("Client"),
                                  "ADAPTER_ARN": attr("Adapter"), "SIGNING_KEY": attr("SigningKey")}, [
        allow("kms:Sign", attr("SigningKey")), allow("lambda:InvokeFunction", attr("Adapter"))]),
    ("Adapter", "adapt", {**secrets_env, "ISSUER": issuer, "SIGNING_KEY": attr("SigningKey"),
                          "MOCK_URL": attr("MockUrl", "FunctionUrl")}, [
        allow("kms:Verify", attr("SigningKey")), secrets_read,
        allow("lambda:InvokeFunctionUrl", attr("Mock"), Condition={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}}),
        allow("lambda:InvokeFunction", attr("Mock"), Condition={"Bool": {"lambda:InvokedViaFunctionUrl": "true"}}),
    ]),
    ("Mock", "mock", secrets_env, [secrets_read]),
    ("Verifier", "handler", {}, [
        allow(["cognito-idp:AdminGetUser", "cognito-idp:AdminSetUserPassword"], attr("Pool")),
        allow("secretsmanager:GetSecretValue", [*secrets_env.values(), ref("OwnerLoginSecret"), ref("CodexLoginSecret")]),
        allow("secretsmanager:PutSecretValue", list(secrets_env.values())),
    ]),
]:
    log = name + "Log"
    resource(log, "Logs::LogGroup", RetentionInDays=180)
    role(name + "Role", "lambda.amazonaws.com", permissions + [
        allow(["logs:CreateLogStream", "logs:PutLogEvents"],
              sub("arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${" + log + "}:log-stream:*"))
    ])
    function_code = verifier_code if name == "Verifier" else code
    resource(name, "Lambda::Function", Handler="index." + handler, Runtime="python3.12",
             Timeout=180 if name == "Verifier" else 25,
             Role=attr(name + "Role"), Code={"ZipFile": function_code}, Environment={"Variables": environment},
             LoggingConfig={"LogGroup": ref(log)})

resource("MockUrl", "Lambda::Url", AuthType="AWS_IAM", TargetFunctionArn=attr("Mock"))
role("GatewayRole", "bedrock-agentcore.amazonaws.com",
     [allow("lambda:InvokeFunction", [attr("Interceptor"), attr("Adapter")])],
     {"StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")},
      "ArnLike": {"aws:SourceArn": sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:gateway/*")}})
resource("Gateway", "BedrockAgentCore::Gateway", Name=ref("AWS::StackName"),
         RoleArn=attr("GatewayRole"), ProtocolType="MCP", AuthorizerType="CUSTOM_JWT",
         AuthorizerConfiguration={"CustomJWTAuthorizer": {
             "DiscoveryUrl": {"Fn::Join": ["", [issuer, "/.well-known/openid-configuration"]]},
             "AllowedClients": [ref("Client")],
             "AllowedScopes": ["aws.cognito.signin.user.admin"],
         }},
         InterceptorConfigurations=[{"InterceptionPoints": ["REQUEST"],
                                     "InputConfiguration": {"PassRequestHeaders": True},
                                     "Interceptor": {"Lambda": {"Arn": attr("Interceptor")}}}])
resource("Target", "BedrockAgentCore::GatewayTarget", Name="mock",
         GatewayIdentifier=attr("Gateway", "GatewayIdentifier"),
         CredentialProviderConfigurations=[{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
         TargetConfiguration={"Mcp": {"Lambda": {"LambdaArn": attr("Adapter"),
                                                "ToolSchema": {"InlinePayload": [{
             "Name": "whoami", "Description": "Return the mock account selected for the caller",
             "InputSchema": {"Type": "object", "Properties": {
                 "_gatewayAuth": {"Type": "string", "Description": "Reserved by the Gateway"}
             }},
         }]}}}})

outputs = {
    "GatewayUrl": attr("Gateway", "GatewayUrl"), "PoolId": ref("Pool"), "ClientId": ref("Client"),
    "OwnerSecret": ref("OwnerSecret"), "CodexSecret": ref("CodexSecret"),
    "MockUrl": attr("MockUrl", "FunctionUrl"), "OwnerLoginSecret": ref("OwnerLoginSecret"),
    "CodexLoginSecret": ref("CodexLoginSecret"),
}
resource("Verify", "CloudFormation::CustomResource", ServiceToken=attr("Verifier"), ServiceTimeout=200,
         ProbeRevision=hashlib.sha256((code + verifier_code).encode()).hexdigest(), **outputs)
resources["Verify"]["DependsOn"] = ["Target", "Interceptor", "Adapter", "Mock", "OwnerUser", "CodexUser"]
outputs["Verification"] = attr("Verify", "Result")

if __name__ == "__main__":
    print(json.dumps({"AWSTemplateFormatVersion": "2010-09-09",
                      "Description": "Isolated Cognito and Pleasanter mock MCP verification",
                      "Resources": resources,
                      "Outputs": {key: {"Value": value} for key, value in outputs.items()}}, indent=2))
