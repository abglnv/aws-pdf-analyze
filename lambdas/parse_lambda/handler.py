import os
import time
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
textract = boto3.client("textract")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["DOCUMENTS_TABLE_NAME"]

# Textract polling: 5-second intervals, up to 5 minutes total
_POLL_INTERVAL = 5
_POLL_MAX_TRIES = 60


def lambda_handler(event, context):
    doc_id = event["doc_id"]
    bucket = event["bucket"]
    s3_key = event["s3_key"]

    table = dynamodb.Table(TABLE_NAME)

    _update_status(table, doc_id, "PARSING")

    try:
        text = _extract_text(bucket, s3_key)

        text_key = f"documents/{doc_id}/intermediate/extracted_text.txt"
        s3.put_object(
            Bucket=bucket,
            Key=text_key,
            Body=text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )

        table.update_item(
            Key={"pk": f"DOC#{doc_id}", "sk": "META"},
            UpdateExpression="SET #s = :parsed, text_s3_key = :tk, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":parsed": "PARSED",
                ":tk": text_key,
                ":now": _now(),
            },
        )

        print(f"doc {doc_id}: parsed OK, text_key={text_key}")
        return {**event, "text_s3_key": text_key}

    except Exception as exc:
        _fail(table, doc_id, exc)
        raise


# Textract

def _extract_text(bucket: str, key: str) -> str:
    resp = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    job_id = resp["JobId"]
    print(f"Textract job {job_id} started")

    for attempt in range(_POLL_MAX_TRIES):
        time.sleep(_POLL_INTERVAL)
        poll = textract.get_document_text_detection(JobId=job_id)
        status = poll["JobStatus"]

        if status == "SUCCEEDED":
            blocks = list(poll["Blocks"])
            next_token = poll.get("NextToken")
            while next_token:
                page = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
                blocks.extend(page["Blocks"])
                next_token = page.get("NextToken")

            lines = [b["Text"] for b in blocks if b["BlockType"] == "LINE"]
            return "\n".join(lines)

        if status == "FAILED":
            raise RuntimeError(f"Textract job failed: {poll.get('StatusMessage', 'unknown')}")

        print(f"Textract job {job_id} still {status} (attempt {attempt + 1}/{_POLL_MAX_TRIES})")

    raise TimeoutError(f"Textract job {job_id} did not complete within {_POLL_MAX_TRIES * _POLL_INTERVAL}s")


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
