# encoding: utf-8
"""Fixed-window rate limit per user (or per client IP for anonymous users),
stored in CKAN's Redis. Every question costs LLM tokens, so this keeps a
public portal from being used as a free LLM proxy.

If Redis is unreachable, the limit is skipped and a warning is logged, so the
assistant keeps working (``fail_open=True``, the default). With
``fail_open=False`` :class:`RateLimitUnavailable` is raised instead and no
question is answered until Redis is back.
"""
import hashlib
import logging
import time

log = logging.getLogger(__name__)

WINDOW_SECONDS = 3600


class RateLimitUnavailable(Exception):
    """Redis could not be used and the limit is configured to fail closed."""


def _key(identity, window):
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return "ckanext:mcp_assistant:rl:{0}:{1}".format(digest, window)


def hit(identity, limit, redis_conn=None, now=None, fail_open=True):
    """Count one question for ``identity``.

    Returns ``(allowed, retry_after_seconds)``. ``limit <= 0`` disables the limit.
    Raises :class:`RateLimitUnavailable` when Redis fails and ``fail_open`` is false.
    """
    if limit <= 0 or not identity:
        return True, 0
    now = int(now if now is not None else time.time())
    window = now // WINDOW_SECONDS
    try:
        if redis_conn is None:
            from ckan.lib.redis import connect_to_redis
            redis_conn = connect_to_redis()
        key = _key(identity, window)
        pipe = redis_conn.pipeline()
        pipe.incr(key)
        pipe.expire(key, WINDOW_SECONDS + 60)
        count = pipe.execute()[0]
    except Exception as e:  # Redis down or misconfigured
        if not fail_open:
            log.error("mcp_assistant rate limit unavailable, question refused (Redis error: %s)", e)
            raise RateLimitUnavailable(str(e))
        log.warning("mcp_assistant rate limit skipped (Redis error: %s)", e)
        return True, 0
    if int(count) > limit:
        return False, (window + 1) * WINDOW_SECONDS - now
    return True, 0

