import base64
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")

BUCKET = os.environ["DOCUMENTS_BUCKET"]
TABLE_NAME = os.environ["DOCUMENTS_TABLE_NAME"]
SFN_ARN = os.environ["STATE_MACHINE_ARN"]
MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB


def lambda_handler(event, context):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "POST").upper()
    path = event.get("rawPath", "/")

    if method == "POST" and path in ("/", "/documents"):
        return _upload(event)

    if method == "GET" and path.startswith("/documents/"):
        doc_id = path.split("/")[-1]
        return _get_status(doc_id)

    return _resp(404, {"error": "not found"})


# POST /documents

def _upload(event):
    is_base64 = event.get("isBase64Encoded", False)
    raw_body = event.get("body") or ""

    pdf_bytes = base64.b64decode(raw_body) if is_base64 else (
        raw_body if isinstance(raw_body, bytes) else raw_body.encode("latin-1")
    )

    if not pdf_bytes:
        return _resp(400, {"error": "empty body"})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    content_type = headers.get("content-type", "")
    if "application/pdf" not in content_type and not pdf_bytes[:4] == b"%PDF":
        return _resp(400, {"error": "body must be a PDF (Content-Type: application/pdf)"})

    if len(pdf_bytes) > MAX_PDF_BYTES:
        return _resp(413, {"error": f"PDF exceeds {MAX_PDF_BYTES // 1024 // 1024} MB limit"})

    doc_id = str(uuid.uuid4())
    now = _now()
    source_key = f"documents/{doc_id}/source.pdf"

    s3.put_object(Bucket=BUCKET, Key=source_key, Body=pdf_bytes, ContentType="application/pdf")

    dynamodb.Table(TABLE_NAME).put_item(Item={
        "pk": f"DOC#{doc_id}",
        "sk": "META",
        "doc_id": doc_id,
        "bucket": BUCKET,
        "source_s3_key": source_key,
        "status": "RECEIVED",
        "created_at": now,
        "updated_at": now,
    })

    execution = sfn.start_execution(
        stateMachineArn=SFN_ARN,
        name=doc_id,
        input=json.dumps({"doc_id": doc_id, "bucket": BUCKET, "s3_key": source_key}),
    )

    print(f"Started execution {execution['executionArn']} for doc {doc_id}")
    return _resp(201, {
        "doc_id": doc_id,
        "message": "processing started",
        "execution_arn": execution["executionArn"],
    })


# GET /documents/{doc_id}

def _get_status(doc_id):
    result = dynamodb.Table(TABLE_NAME).get_item(Key={"pk": f"DOC#{doc_id}", "sk": "META"})
    item = result.get("Item")
    if not item:
        return _resp(404, {"error": "document not found"})

    body = {
        "doc_id": item["doc_id"],
        "status": item["status"],
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }

    if item.get("error_message"):
        body["error_message"] = item["error_message"]

    if item["status"] == "COMPLETED" and item.get("result_s3_key"):
        s3_client = boto3.client("s3")
        presigned = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": item["bucket"], "Key": item["result_s3_key"]},
            ExpiresIn=7200,
        )
        body["result_url"] = presigned
        body["result_s3_key"] = item["result_s3_key"]

    return _resp(200, body)


# helpers 

def _now():
    return datetime.now(timezone.utc).isoformat()


def _resp(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
