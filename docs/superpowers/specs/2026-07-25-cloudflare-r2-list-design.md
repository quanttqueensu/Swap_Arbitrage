# Cloudflare R2 Object Listing Design

## Goal

Add a small, read-only Python utility that confirms a Cloudflare API-token
connection and prints the object keys (file names, including path-like prefixes)
stored in accessible R2 buckets.

## Interface

The utility will be named `cloudflare_r2_list.py` and will use only Python's
standard library. It reads:

- `CLOUDFLARE_API_TOKEN`, a Cloudflare API token with Workers R2 Storage Read
  permission; and
- `CLOUDFLARE_ACCOUNT_ID`, the Cloudflare account identifier that owns the R2
  buckets.

The token is supplied only through the process environment. It is never stored
in source code, written to disk, included in an error, or printed.

Running `python cloudflare_r2_list.py` will verify the token, discover every R2
bucket visible to it, and print each bucket name followed by all object keys in
that bucket. Empty buckets are identified explicitly. Output contains names
only; the utility does not download or display object contents.

## API flow

The utility sends authenticated `GET` requests to Cloudflare API v4 using an
`Authorization: Bearer` header:

1. `GET /user/tokens/verify` confirms that the token is active.
2. `GET /accounts/{account_id}/r2/buckets` discovers accessible buckets.
3. `GET /accounts/{account_id}/r2/buckets/{bucket_name}/objects` lists object
   keys for each bucket.

Bucket and object cursor pagination is followed until no continuation cursor is
returned. Bucket names are URL-encoded when inserted into request paths.

## Error handling

The utility exits with a nonzero status and a concise message when either
environment variable is absent, the token is inactive, an HTTP or network
request fails, Cloudflare returns `success: false`, or the response shape is
invalid. Cloudflare error messages may be shown, but request headers and token
values must never appear in diagnostics.

A token that verifies successfully but lacks Workers R2 Storage Read permission
will therefore produce a clear authorization failure when the bucket or object
request is made.

## Testing

Tests will use the repository's existing `unittest` style and injected HTTP
responses; they will not require a live Cloudflare account. Coverage will
include:

- missing configuration;
- active-token verification;
- inactive-token rejection;
- bucket and object cursor pagination;
- empty buckets;
- extraction of object keys only; and
- sanitized Cloudflare API failures.

After the automated tests pass, the utility's help/setup instructions and
syntax will be checked locally without requiring or fabricating a real token.

## Non-goals

- Do not download, upload, modify, or delete R2 objects.
- Do not add `boto3`, Wrangler, the Cloudflare SDK, or another dependency.
- Do not accept a token as a command-line argument.
- Do not inspect D1, KV, Workers, DNS, or other Cloudflare products.

## Authoritative references

- Cloudflare R2 List Buckets API:
  <https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/list/>
- Cloudflare R2 List Objects API:
  <https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/methods/list>
- Cloudflare API Token Verification:
  <https://developers.cloudflare.com/api/resources/user/subresources/tokens/methods/verify/>
