# Lossless response delivery

AUREN must not silently truncate code, patches, evidence, or final answers when a
model, transport, tool, or UI response budget is approached. The reusable
primitive in `portable.response_delivery` treats chunking as transport only:
it preserves the original text exactly and provides end-to-end integrity checks.

## Contract

- Use UTF-8 byte limits as well as any character/token limits imposed by the
  receiving adapter. The adapter must set `max_bytes` below its actual safe
  payload ceiling, leaving room for framing metadata.
- Prefer paragraph, line, and whitespace boundaries; split long code lines or
  unbroken strings at Unicode code-point boundaries when required.
- Emit a manifest and ordered chunk metadata: response digest, chunk count,
  chunk index, per-chunk digest, total characters, and total UTF-8 bytes.
- Resume from the last **acknowledged** chunk. Do not assume that sending a
  chunk means the user or downstream consumer received it.
- Reassemble only after validating order, uniqueness, per-chunk digest, manifest
  lengths, and the whole-response digest. Missing, duplicated, reordered, or
  corrupted chunks fail closed.
- Empty output still has an explicit completion chunk.
- Keep original artifacts (files/patches) canonical when the host supports
  attachments or durable artifact references. A text preview is not a substitute
  for a missing file. Never invent an artifact path or claim delivery without
  a verified reference.
- Never use sampling or summarization for final code, security constraints,
  acceptance criteria, failure evidence, migration/rollback instructions, or
  any task whose acceptance requires exhaustive coverage. Sampling is only an
  explicitly permitted exploration technique.
- If a platform has no acknowledgement/resume mechanism, mark delivery as
  unconfirmed and provide stable part labels plus a clear continuation cursor.
  Do not claim that the entire response was delivered.

## Integration sequence

1. Construct the complete result or artifact first; do not generate a knowingly
   incomplete final response as the canonical result.
2. Estimate the target adapter's safe output budget and chunk the result before
   serialization.
3. Deliver numbered chunks with stable response ID and total count.
4. Persist the last acknowledged index in the existing task/progress journal
   where durable delivery is required; do not create a parallel delivery store.
5. Verify receipt or reconstruct the response from the chunks and compare the
   whole-response digest.
6. Mark the task complete only after artifact validation and delivery state are
   both complete. If interrupted, resume at the next unacknowledged chunk.

## Boundaries and rollback

This module is a deterministic transport primitive. It does not prove that a
provider or chat UI will accept every chunk, does not increase the platform's
maximum response size, and does not itself persist acknowledgements. Integrate
it through the canonical runtime and adapter lifecycle before claiming end-to-end
delivery guarantees.

Rollback: remove the adapter integration and return to the prior bounded-output
path; retain artifact integrity checks and never restore silent truncation.
