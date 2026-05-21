from __future__ import annotations

from ..models import Bucket, SurfaceResult, TriageResult
from .blocks import digest_blocks, respond_blocks, split_digest, uncertain_ignore_blocks
from .client import get_channel_id, get_client, is_configured
from .types import DispatchSummary


def dispatch(results: list[TriageResult]) -> DispatchSummary:
    """Route triage results to Slack.

    Buckets, by `SurfaceResult.decision.bucket`:
      • respond → individual realtime DM post (one per mail)
      • notify  → batched digest (split into ≤24-row chunks)
      • ignore  → uncertain-band digest. These are mails the gate refused to
                  auto-mark (low confidence, or no opt-in rule). They are
                  already persisted, so if we don't surface them here they
                  are effectively lost from the user's view.
    """
    surface = [r for r in results if isinstance(r, SurfaceResult)]
    realtime = [r for r in surface if r.decision.bucket == Bucket.RESPOND]
    digest = [r for r in surface if r.decision.bucket == Bucket.NOTIFY]
    uncertain = [r for r in surface if r.decision.bucket == Bucket.IGNORE]
    skipped = len(results) - len(realtime) - len(digest) - len(uncertain)

    if not is_configured():
        return DispatchSummary(
            realtime_posts=0,
            digest_mails=0,
            skipped=skipped + len(realtime) + len(digest) + len(uncertain),
        )

    if not realtime and not digest and not uncertain:
        return DispatchSummary(realtime_posts=0, digest_mails=0, skipped=skipped)

    client = get_client()
    channel = get_channel_id()

    realtime_posts = 0
    for r in realtime:
        client.chat_postMessage(
            channel=channel,
            blocks=respond_blocks(r),
            text=f"Mail needs response: {r.email.subject}",
            unfurl_links=False,
            unfurl_media=False,
        )
        realtime_posts += 1

    digest_mails = 0
    for chunk in split_digest(digest):
        client.chat_postMessage(
            channel=channel,
            blocks=digest_blocks(chunk),
            text=f"Mail digest · {len(chunk)} item(s)",
            unfurl_links=False,
            unfurl_media=False,
        )
        digest_mails += len(chunk)

    for chunk in split_digest(uncertain):
        client.chat_postMessage(
            channel=channel,
            blocks=uncertain_ignore_blocks(chunk),
            text=f"Uncertain auto-marks · {len(chunk)} item(s)",
            unfurl_links=False,
            unfurl_media=False,
        )
        digest_mails += len(chunk)

    return DispatchSummary(
        realtime_posts=realtime_posts,
        digest_mails=digest_mails,
        skipped=skipped,
    )
