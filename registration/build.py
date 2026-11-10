"""Emit the deployable CloudFormation template to stdout."""
import json
from pathlib import Path

root = Path(__file__).parent


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def attr(name, attribute):
    return {"Fn::GetAtt": [name, attribute]}


def resource(type_name, **properties):
    return {"Type": type_name, "Properties": properties}


endpoint = attr("Api", "ApiEndpoint")
callback = sub("${Api.ApiEndpoint}/")
issuer = sub("https://cognito-idp.${AWS::Region}.${AWS::URLSuffix}/${UserPoolId}")
prefix = sub("pleasanter-mcp/${Environment}/")
scopes = {action: sub(f"${{Api.ApiEndpoint}}/keys.{action}") for action in ("read", "write", "delete")}
code = (root / "handler.py").read_text(encoding="utf-8")
code += "\nPAGE = " + repr((root / "index.html").read_text(encoding="utf-8")) + "\n"

resources = {
    "Api": resource("AWS::ApiGatewayV2::Api", Name=ref("AWS::StackName"), ProtocolType="HTTP"),
    "ScopeServer": resource(
        "AWS::Cognito::UserPoolResourceServer", UserPoolId=ref("UserPoolId"),
        Identifier=endpoint, Name=ref("AWS::StackName"),
        Scopes=[{"ScopeName": f"keys.{action}", "ScopeDescription": f"{action} own key registration"}
                for action in scopes],
    ),
    "AppClient": resource(
        "AWS::Cognito::UserPoolClient", UserPoolId=ref("UserPoolId"), ClientName=ref("AWS::StackName"),
        GenerateSecret=False, AllowedOAuthFlowsUserPoolClient=True, AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=["openid", *scopes.values()],
        CallbackURLs=[callback], LogoutURLs=[callback],
        SupportedIdentityProviders=[ref("IdentityProvider")],
        ReadAttributes=["email"], WriteAttributes=["email"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"], PreventUserExistenceErrors="ENABLED",
        EnableTokenRevocation=True, AccessTokenValidity=15, TokenValidityUnits={"AccessToken": "minutes"},
    ),
    "Authorizer": resource(
        "AWS::ApiGatewayV2::Authorizer", ApiId=ref("Api"), Name="Cognito",
        AuthorizerType="JWT", IdentitySource=["$request.header.Authorization"],
        JwtConfiguration={"Audience": [{"Fn::If": ["NativeLogin", ref("AppClient"), endpoint]}], "Issuer": issuer},
    ),
    "Logs": resource(
        "AWS::Logs::LogGroup", LogGroupName=sub("/aws/lambda/${AWS::StackName}"), RetentionInDays=180,
    ),
    "Role": resource(
        "AWS::IAM::Role",
        AssumeRolePolicyDocument={"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole",
        }]},
        Policies=[{"PolicyName": "OwnEnvironmentKeysAndLogs", "PolicyDocument": {
            "Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue", "secretsmanager:CreateSecret",
                                             "secretsmanager:PutSecretValue"],
                 "Resource": sub("arn:${AWS::Partition}:secretsmanager:${AWS::Region}:${AWS::AccountId}"
                                 ":secret:pleasanter-mcp/${Environment}/*")},
                {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                 "Resource": attr("Logs", "Arn")},
            ],
        }}],
    ),
    "Function": resource(
        "AWS::Lambda::Function", FunctionName=ref("AWS::StackName"),
        Runtime="python3.13", Handler="index.handler", Role=attr("Role", "Arn"), Timeout=10,
        LoggingConfig={"LogGroup": ref("Logs")},
        Code={"ZipFile": code}, Environment={"Variables": {
            "CLIENT_ID": ref("AppClient"), "ISSUER": issuer,
            "AUDIENCE": {"Fn::If": ["NativeLogin", ref("AppClient"), endpoint]},
            "SECRET_PREFIX": prefix, "LOGIN_ORIGIN": ref("LoginOrigin"),
            "IDENTITY_PROVIDER": ref("IdentityProvider"),
            "SCOPES": {"Fn::Join": [" ", ["openid", *scopes.values()]]},
        }},
    ),
    "Integration": resource(
        "AWS::ApiGatewayV2::Integration", ApiId=ref("Api"), IntegrationType="AWS_PROXY",
        IntegrationUri=attr("Function", "Arn"), PayloadFormatVersion="2.0",
    ),
    "Permission": resource(
        "AWS::Lambda::Permission", Action="lambda:InvokeFunction", FunctionName=ref("Function"),
        Principal="apigateway.amazonaws.com",
        SourceArn=sub("arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/*"),
    ),
    "Stage": resource(
        "AWS::ApiGatewayV2::Stage", ApiId=ref("Api"), StageName="$default", AutoDeploy=True,
        DefaultRouteSettings={"ThrottlingBurstLimit": 10, "ThrottlingRateLimit": 5},
    ),
}
resources["AppClient"]["DependsOn"] = "ScopeServer"

for logical_id, route, action in [
    ("Page", "GET /", None),
    ("KeyStatus", "GET /me/pleasanter-key/status", "read"),
    ("KeyPut", "PUT /me/pleasanter-key", "write"),
    ("KeyDelete", "DELETE /me/pleasanter-key", "delete"),
]:
    auth = {"AuthorizationType": "NONE"} if action is None else {
        "AuthorizationType": "JWT", "AuthorizerId": ref("Authorizer"),
        "AuthorizationScopes": [scopes[action]],
    }
    resources[logical_id] = resource(
        "AWS::ApiGatewayV2::Route", ApiId=ref("Api"), RouteKey=route,
        Target=sub("integrations/${Integration}"), **auth,
    )

print(json.dumps({
    "AWSTemplateFormatVersion": "2010-09-09",
    "Description": "Self-service Pleasanter API key registration using an existing Cognito pool",
    "Parameters": {
        "UserPoolId": {"Type": "String", "AllowedPattern": "[a-z0-9-]+_[A-Za-z0-9]+"},
        "LoginOrigin": {"Type": "String", "AllowedPattern": "https://[a-z0-9-]+(\\.[a-z0-9-]+)+"},
        "IdentityProvider": {"Type": "String", "AllowedPattern": "[A-Za-z0-9_-]{1,32}"},
        "Environment": {"Type": "String", "AllowedPattern": "[a-z0-9][a-z0-9-]{0,31}"},
    },
    "Conditions": {"NativeLogin": {"Fn::Equals": [ref("IdentityProvider"), "COGNITO"]}},
    "Resources": resources,
    "Outputs": {"RegistrationUrl": {"Value": callback}},
}))
