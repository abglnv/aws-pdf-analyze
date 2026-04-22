# AWS PDF Analyzer

Serverless pipeline that takes a PDF, extracts the text with AWS Textract, then sends it to OpenAI to pull out structured data — names (with IINs), decisions, and reasons. Everything runs on Lambda + Step Functions, results land in S3.

## How it works

```
POST /documents
      ↓
  api_handler  →  S3 (raw PDF)  →  Step Functions
                                        ↓
                                   parse_lambda  (Textract)
                                        ↓
                                  analyze_lambda  (OpenAI)
                                        ↓
                                   S3 (result JSON)
```

Status is tracked in DynamoDB throughout. Once complete, the result JSON has:
- `fullNames` — array of `{ name, iin }` found in the document
- `decision` — any conclusion or resolution
- `reason` — justification for the decision

## Data layout

**S3** — everything lives under `documents/{doc_id}/`:

| Key | What |
|-----|------|
| `documents/{doc_id}/source.pdf` | original uploaded PDF |
| `documents/{doc_id}/intermediate/extracted_text.txt` | raw text from Textract |
| `documents/{doc_id}/result/final_output.json` | final structured output |

**DynamoDB** — one item per document, `pk = DOC#{doc_id}`, `sk = META`:

| Field | Description |
|-------|-------------|
| `doc_id` | UUID generated on upload |
| `status` | `RECEIVED` → `PARSING` → `PARSED` → `ANALYZING` → `COMPLETED` (or `FAILED`) |
| `source_s3_key` | path to the uploaded PDF in S3 |
| `text_s3_key` | path to extracted text (set after Textract finishes) |
| `result_s3_key` | path to the result JSON (set after OpenAI finishes) |
| `created_at` / `updated_at` | ISO 8601 timestamps |
| `error_message` | only present on `FAILED`, capped at 500 chars |

`result_url` in the GET response is a presigned S3 URL generated on the fly (valid 2 hours), not stored in DynamoDB.

## Setup

1. Create a DynamoDB table (pk + sk as keys)
2. Create an S3 bucket
3. Create an empty Step Function
4. Create **api_handler** lambda, upload the zip, set env vars:
   - `DOCUMENTS_BUCKET`, `DOCUMENTS_TABLE_NAME`, `STATE_MACHINE_ARN`
5. Create **parse_lambda** lambda, upload the zip, set env vars:
   - `DOCUMENTS_TABLE_NAME`
6. Create **analyze_lambda** lambda, upload the zip, set env vars:
   - `DOCUMENTS_TABLE_NAME`, `OPENAI_API_KEY`, `OPENAI_MODEL` (default: `gpt-4o-mini`)
7. Attach IAM roles from `/permissions` to each lambda
8. Upload `/statemachine/pipeline.asl.json` to the Step Function, fill in the lambda ARNs in `.parameters.FunctionName`
9. Create an API Gateway with `POST /documents` and `GET /documents/{doc_id}`, both pointing to api_handler

## Test

Upload a document:

```bash
curl -X POST https://<id>.execute-api.<region>.amazonaws.com/documents \
    -H "Content-Type: application/pdf" \
    --data-binary @test_pdfs/loan_request_seitkali.pdf
```

Response:

```json
{"doc_id": "...", "message": "processing started", "execution_arn": "..."}
```

Check status:

```bash
curl https://<id>.execute-api.<region>.amazonaws.com/documents/<doc_id>
```

Response when done:

```json
{
  "doc_id": "...",
  "status": "COMPLETED",
  "created_at": "2026-04-22T13:41:40.752680+00:00",
  "updated_at": "2026-04-22T13:41:58.553909+00:00",
  "result_url": "...",
  "result_s3_key": "documents/<doc_id>/result/final_output.json"
}
```

Open `result_url` to see the extracted data. There are a few test PDFs in `/test_pdfs` to try out.
