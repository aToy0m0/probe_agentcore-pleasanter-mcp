"""Lambda handlers for the isolated Cognito-to-MCP probe."""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError


USERS = {"owner": "OWNER_SECRET", "codex": "CODEX_SECRET"}
ALG = "RSASSA_PKCS1_V1_5_SHA_256"
JWS_HEADER = b'{"alg":"RS256","typ":"probe+jws"}'


def encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def decode(raw):
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def secret(username):
    arn = os.environ[USERS[username]]
    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])


def intercept(event, context):
    incoming = event["mcp"]["gatewayRequest"]
    body = incoming["body"]
    headers = {key.lower(): value for key, value in incoming.get("headers", {}).items()}
    auth = headers.get("authorization", "")
    try:
        scheme, token = auth.split(" ", 1)
        if scheme.lower() != "bearer":
            raise ValueError()
        # GetUser validates the signature, token type, expiry and user status.
        user = boto3.client("cognito-idp").get_user(AccessToken=token)
        claims = json.loads(decode(token.split(".")[1].encode("ascii")))
        attributes = {attr["Name"]: attr["Value"] for attr in user["UserAttributes"]}
        if (
            claims["iss"] != os.environ["ISSUER"]
            or claims["client_id"] != os.environ["CLIENT_ID"]
            or claims["sub"] != attributes["sub"]
            or user["Username"] not in USERS
        ):
            raise ValueError()
    except (ValueError, KeyError, IndexError, ClientError):
        return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayResponse": {
            "statusCode": 403,
            "body": {"jsonrpc": "2.0", "id": body.get("id"),
                     "error": {"code": -32001, "message": "Probe identity rejected"}},
        }}}

    method = body["method"]
    if method in ("tools/call", "tools/list"):
        # This probe has one fixed, read-only tool and no business arguments.
        if method == "tools/call" and body["params"]["name"] != "mock___whoami":
            raise ValueError("Unexpected tool")
        payload = {"iss": claims["iss"], "sub": attributes["sub"],
                   "user": user["Username"], "method": method, "exp": int(time.time()) + 30}
        signing_input = encode(JWS_HEADER) + b"." + encode(json.dumps(payload).encode())
        signature = boto3.client("kms").sign(
            KeyId=os.environ["SIGNING_KEY"], Message=signing_input,
            MessageType="RAW", SigningAlgorithm=ALG,
        )["Signature"]
        arguments = {"_gatewayAuth": (signing_input + b"." + encode(signature)).decode()}
        if method == "tools/list":
            invocation = boto3.client("lambda").invoke(
                FunctionName=os.environ["ADAPTER_ARN"], Payload=json.dumps(arguments).encode())
            if "FunctionError" in invocation:
                raise RuntimeError("Adapter failed to list mock tools")
            result = json.load(invocation["Payload"])
            return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayResponse": {
                "statusCode": 200,
                "body": {"jsonrpc": "2.0", "id": body["id"], "result": result},
            }}}
        body["params"]["arguments"] = arguments
    return {"interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayRequest": {"body": body}}}


def call_mock(method, params, api_key, request_id=None):
    url = os.environ["MOCK_URL"].rstrip("/") + "/mcp"
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        body["id"] = request_id
    data = json.dumps(body).encode()
    signed = AWSRequest(method="POST", url=url, data=data, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "X-API-Key": api_key, "X-Amz-Content-Sha256": hashlib.sha256(data).hexdigest(),
        "MCP-Protocol-Version": "2025-03-26",
    })
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, "lambda", os.environ["AWS_REGION"]).add_auth(signed)
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=dict(signed.headers)),
                                timeout=5) as response:
        return json.load(response)["result"] if request_id is not None else None


def adapt(event, context):
    try:
        envelope = event["_gatewayAuth"].encode("ascii")
        header, payload, signature = envelope.split(b".")
        if len(envelope) > 2048 or decode(header) != JWS_HEADER:
            raise ValueError()
        valid = boto3.client("kms").verify(
            KeyId=os.environ["SIGNING_KEY"], Message=header + b"." + payload,
            Signature=decode(signature), MessageType="RAW", SigningAlgorithm=ALG,
        )["SignatureValid"]
        claims = json.loads(decode(payload))
        if (not valid or claims["exp"] <= time.time() or claims["iss"] != os.environ["ISSUER"]
                or claims["method"] not in ("tools/list", "tools/call") or claims["user"] not in USERS):
            raise ValueError()
        record = secret(claims["user"])
        if record["sub"] != claims["sub"]:
            raise ValueError()
    except (ValueError, KeyError, AttributeError, ClientError):
        raise ValueError("Signed probe identity or API key binding rejected") from None

    call_mock("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "probe-adapter", "version": "1"}},
              record["apiKey"], 1)
    call_mock("notifications/initialized", {}, record["apiKey"])
    if claims["method"] == "tools/list":
        catalog = call_mock("tools/list", {}, record["apiKey"], 2)
        # Gateway can route only the tools registered on its Lambda target.
        return {"tools": [{**tool, "name": "mock___" + tool["name"]}
                          for tool in catalog["tools"] if tool["name"] == "whoami"]}
    return call_mock("tools/call", {"name": "whoami", "arguments": {}}, record["apiKey"], 2)


def mock(event, context):
    if event["rawPath"] != "/mcp" or event["requestContext"]["http"]["method"] != "POST":
        return {"statusCode": 404, "body": ""}
    api_key = event.get("headers", {}).get("x-api-key", "")
    user = next((username for username in USERS if api_key and
                 hmac.compare_digest(api_key, secret(username)["apiKey"])), None)
    if user is None:
        return {"statusCode": 403, "body": ""}

    request = json.loads(event["body"])
    method = request["method"]
    if method == "notifications/initialized":
        return {"statusCode": 202, "body": ""}
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "pleasanter-mock", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "whoami", "description": "Return the account identified by the mock API key",
                             "inputSchema": {"type": "object"}}]}
    elif method == "tools/call" and request["params"]["name"] == "whoami":
        result = {"content": [{"type": "text", "text": "mock-account:" + user}]}
    else:
        return {"statusCode": 400, "body": ""}
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})}
