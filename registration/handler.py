import base64
import hashlib
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

secrets = boto3.client("secretsmanager")


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def page():
    config = {key: os.environ[env] for key, env in {
        "domain": "LOGIN_ORIGIN", "client": "CLIENT_ID",
        "provider": "IDENTITY_PROVIDER", "scopes": "SCOPES",
    }.items()}
    html = PAGE.replace("__CONFIG__", json.dumps(config).replace("<", "\\u003c"))
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; "
                f"connect-src 'self' {config['domain']}; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'"
            ),
        },
        "body": html,
    }


def save(name, record):
    payload = json.dumps(record)
    try:
        secrets.put_secret_value(SecretId=name, SecretString=payload)
    except secrets.exceptions.ResourceNotFoundException:
        try:
            secrets.create_secret(Name=name, SecretString=payload)
        except secrets.exceptions.ResourceExistsException:
            secrets.put_secret_value(SecretId=name, SecretString=payload)


def handler(event, context):
    route = event.get("routeKey")
    if route == "GET /":
        return page()
    if route not in {
        "GET /me/pleasanter-key/status", "PUT /me/pleasanter-key", "DELETE /me/pleasanter-key"
    }:
        return respond(404, {"error": "not_found"})

    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    if (claims.get("iss") != os.environ["ISSUER"] or claims.get("token_use") != "access"
            or claims.get("client_id") != os.environ["CLIENT_ID"]
            or (claims.get("aud") or claims.get("client_id")) != os.environ["AUDIENCE"]
            or not isinstance(sub, str) or not 1 <= len(sub) <= 256):
        return respond(401, {"error": "unauthorized"})
    digest = hashlib.sha256((claims["iss"] + "\n" + sub).encode()).hexdigest()
    name = os.environ["SECRET_PREFIX"] + digest

    try:
        if route.startswith("GET "):
            try:
                value = secrets.get_secret_value(SecretId=name)
            except secrets.exceptions.ResourceNotFoundException:
                return respond(200, {"registered": False, "updatedAt": None})
            record = json.loads(value["SecretString"])
            return respond(200, {
                "registered": record["state"] == "active", "updatedAt": record["updatedAt"],
            })

        now = datetime.now(timezone.utc).isoformat()
        if route.startswith("DELETE "):
            try:
                secrets.put_secret_value(SecretId=name, SecretString=json.dumps({
                    "schemaVersion": 1, "state": "disabled", "updatedAt": now,
                }))
            except secrets.exceptions.ResourceNotFoundException:
                pass
            return respond(200, {"registered": False, "updatedAt": now})

        body = event.get("body")
        if event.get("isBase64Encoded") or not isinstance(body, str) or len(body.encode()) > 4096:
            return respond(400, {"error": "invalid_request"})
        try:
            data = json.loads(body)
        except ValueError:
            return respond(400, {"error": "invalid_request"})
        key = data.get("apiKey") if isinstance(data, dict) and set(data) == {"apiKey"} else None
        if not isinstance(key, str) or not 1 <= len(key) <= 1024 or any(
            not 33 <= ord(character) <= 126 for character in key
        ):
            return respond(400, {"error": "invalid_key"})
        save(name, {"schemaVersion": 1, "apiKey": key, "updatedAt": now, "state": "active"})
        return respond(200, {"registered": True, "updatedAt": now})
    except (ClientError, ValueError, KeyError, TypeError) as error:
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        print(json.dumps({"requestId": context.aws_request_id, "route": route, "error": code}))
        return respond(503, {"error": "temporarily_unavailable"})
