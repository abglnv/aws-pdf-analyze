import json
import os
import urllib.request
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["DOCUMENTS_TABLE_NAME"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_EXTRACTION_PROMPT = """\
You are a document analysis assistant. Extract structured information from the document text below.

Return a JSON object with exactly these fields:
- fullNames: array of objects, each with "name" (string) and "iin" (string, empty string if not found)
- decision: string — any decision or conclusion found in the document, empty string if none
- reason: string — reason or justification for the decision, empty string if none

Rules:
- Return ONLY valid JSON. No markdown fences, no explanation.
- If a field has no data, use an empty array or empty string.

Document text:
{text}
"""


def lambda_handler(event, context):
    doc_id = event["doc_id"]
    bucket = event["bucket"]
    text_s3_key = event["text_s3_key"]

    table = dynamodb.Table(TABLE_NAME)

    _update_status(table, doc_id, "ANALYZING")

    try:
        obj = s3.get_object(Bucket=bucket, Key=text_s3_key)
        text = obj["Body"].read().decode("utf-8")

        structured = _call_bedrock(text)
        structured["doc_id"] = doc_id
        structured["schema_version"] = "1.0"

        result_key = f"documents/{doc_id}/result/final_output.json"
        s3.put_object(
            Bucket=bucket,
            Key=result_key,
            Body=json.dumps(structured, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        table.update_item(
            Key={"pk": f"DOC#{doc_id}", "sk": "META"},
            UpdateExpression="SET #s = :completed, result_s3_key = :rk, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":completed": "COMPLETED",
                ":rk": result_key,
                ":now": _now(),
            },
        )

        print(f"doc {doc_id}: analysis complete, result_key={result_key}")
        return {**event, "result_s3_key": result_key, "status": "COMPLETED"}

    except Exception as exc:
        _fail(table, doc_id, exc)
        raise


# OpenAI via raw HTTP (no sdk, no dependencies)

def _call_bedrock(text: str) -> dict:
    capped = text[:12000]
    payload = json.dumps({
        "model": OPENAI_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": _EXTRACTION_PROMPT.format(text=capped)}],
        "max_tokens": 1024,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    return json.loads(result["choices"][0]["message"]["content"])


# helpers

def _now():
    return datetime.now(timezone.utc).isoformat()


def _update_status(table, doc_id: str, status: str):
    table.update_item(
        Key={"pk": f"DOC#{doc_id}", "sk": "META"},
        UpdateExpression="SET #s = :s, updated_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":now": _now()},
    )


def _fail(table, doc_id: str, exc: Exception):
    table.update_item(
        Key={"pk": f"DOC#{doc_id}", "sk": "META"},
        UpdateExpression="SET #s = :failed, error_message = :em, updated_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":failed": "FAILED",
            ":em": str(exc)[:500],
            ":now": _now(),
        },
    )
