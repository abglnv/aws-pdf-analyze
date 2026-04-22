# AWS PDF ANALYZER 

## Getting started 

/lambdas folder - folder with 3 lambda functions: analyze_lambda, api_handler and parse_lambda. 

/statemachine - the code of the stepfunction. 

to setup the project you will need to:

1. create the dynamodb table
2. create the s3 bucket
3. create the emtpy step function
4. create the lambda for api_handler, upload the code and add the env variables: DOCUMENTS_BUCKET, DOCUMENTS_TABLE_NAME, STATE_MACHINE_ARN
5. create the lambda for parse_lambda, upload the code and add the env variables: DOCUMENTS_TABLE_NAME
6. create the lambda for analyze_lambda, upload the code and add the env variables: DOCUMENTS_TABLE_NAME, OPENAI_API_KEY, OPENAI_MODEL
7. provide the permissions to roles in IAM (/permissions folder)
8. upload the code of the statemachine to the step function, fill in the ARNs of lambda functions (.parametrs.FunctionName)
9. create the api gateway, POST /documents and GET/documents/{doc_id} and link to the api_handler lambda function.

## Test 

Upload the document:

```bash
curl -X POST https://id.execute-api.region.amazonaws.com/documents \
    -H "Content-Type: application/pdf" \
    --data-binary @test_pdfs/loan_request_seitkali.pdf
```

Expected output:

```json
{"doc_id": string, "message": "processing started", "execution_arn": string}
```



Get the document status:

GET https://3apkygyhz6.execute-api.eu-central-1.amazonaws.com/documents/{doc_id}

Expected output: 

```json
{"doc_id": string, "status": "COMPLETED", "created_at": "2026-04-22T13:41:40.752680+00:00", "updated_at": "2026-04-22T13:41:58.553909+00:00", "result_url": url, "result_s3_key": "documents/id/result/final_output.json"}
```

By opening the result_url you will be able to view the processed dcument data
