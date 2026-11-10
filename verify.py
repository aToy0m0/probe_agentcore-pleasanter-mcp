"""Bind mock keys to test subjects and check the deployed request path."""

import json
import urllib.error
import urllib.request

import boto3


def rpc(url, method, params=None, token=None, session_id=None, notification=False):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-03-26"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        body["id"] = 1
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode()
        if response.headers.get_content_type() == "text/event-stream":
            raw = next(line[6:] for line in raw.splitlines() if line.startswith("data: "))
        return (json.loads(raw) if raw else None), response.headers.get("Mcp-Session-Id")


def require_http_denial(url, token=None):
    try:
        rpc(url, "tools/list", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return
        raise
    raise RuntimeError("Expected access to be denied")


def require_result(message):
    if "error" in message or message.get("result", {}).get("isError"):
        raise RuntimeError("Gateway returned an MCP error")
    return message["result"]


def run(outputs):
    gateway = outputs["GatewayUrl"]
    cognito = boto3.client("cognito-idp")
    secrets = boto3.client("secretsmanager")

    require_http_denial(gateway)
    require_http_denial(gateway, token="invalid-probe-token")
    require_http_denial(outputs["MockUrl"].rstrip("/") + "/mcp")
    print("PASS anonymous and invalid-token Gateway calls, and anonymous mock calls are rejected")

    for username in ("owner", "codex"):
        logical = username.capitalize()
        password = json.loads(secrets.get_secret_value(SecretId=outputs[logical + "LoginSecret"])["SecretString"])["password"]
        user = cognito.admin_get_user(UserPoolId=outputs["PoolId"], Username=username)
        attributes = {attr["Name"]: attr["Value"] for attr in user["UserAttributes"]}
        arn = outputs[logical + "Secret"]
        record = json.loads(secrets.get_secret_value(SecretId=arn)["SecretString"])
        if record.get("sub", attributes["sub"]) != attributes["sub"]:
            raise RuntimeError("Existing key binding belongs to a different user")
        if "sub" not in record:
            record["sub"] = attributes["sub"]
            secrets.put_secret_value(SecretId=arn, SecretString=json.dumps(record))
        cognito.admin_set_user_password(UserPoolId=outputs["PoolId"], Username=username,
                                        Password=password, Permanent=True)
        auth = cognito.initiate_auth(
            ClientId=outputs["ClientId"], AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password})
        token = auth["AuthenticationResult"]["AccessToken"]
        del password, auth
        message, session_id = rpc(gateway, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"}}, token)
        require_result(message)
        rpc(gateway, "notifications/initialized", token=token, session_id=session_id, notification=True)
        message, _ = rpc(gateway, "tools/list", token=token, session_id=session_id)
        tools = require_result(message)["tools"]
        if not any(tool["name"] == "mock___whoami" and
                   tool.get("description") == "Return the account identified by the mock API key" for tool in tools):
            raise RuntimeError("Mock tool catalog was not propagated")
        for arguments in ({}, {"_gatewayAuth": "invalid-probe-context"}):
            message, _ = rpc(gateway, "tools/call", {"name": "mock___whoami", "arguments": arguments},
                             token, session_id)
            result = require_result(message)
            other = "codex" if username == "owner" else "owner"
            encoded = json.dumps(result)
            if "mock-account:" + username not in encoded or "mock-account:" + other in encoded:
                raise RuntimeError("Unexpected mock account in response")
        print(f"PASS {username}: login, discovery, correct mock key and reserved argument replacement")


def handler(event, context):
    result = {
        "Status": "SUCCESS", "Reason": "Probe checks complete",
        "PhysicalResourceId": event.get("PhysicalResourceId", context.log_stream_name),
        **{key: event[key] for key in ("StackId", "RequestId", "LogicalResourceId")},
        "Data": {"Result": "anonymous and invalid denied; owner and codex isolated; reserved argument replaced"},
    }
    if event["RequestType"] != "Delete":
        try:
            run(event["ResourceProperties"])
        except Exception as exc:
            # Keep token values, response payloads and signed URLs out of logs and CFN events.
            detail = type(exc).__name__
            if isinstance(exc, urllib.error.HTTPError):
                detail += f" HTTP {exc.code}"
            elif hasattr(exc, "response"):
                detail += " " + exc.response.get("Error", {}).get("Code", "")
            elif type(exc) is RuntimeError:
                detail += " " + str(exc)
            result.update(Status="FAILED", Reason=f"{detail}; log stream {context.log_stream_name}", Data={})
    payload = json.dumps(result).encode()
    request = urllib.request.Request(event["ResponseURL"], data=payload, method="PUT",
                                     headers={"Content-Type": ""})
    with urllib.request.urlopen(request, timeout=10):
        pass
