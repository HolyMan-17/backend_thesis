import asyncio
import logging
from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)
from requests.exceptions import ConnectionError, HTTPError

logger = logging.getLogger("smartsaver.push")

# Reuse a single PushClient instance across calls
_push_client = PushClient()


def _send_push_sync(token: str, title: str, body: str, extra: dict | None = None) -> bool:
    """
    Synchronous push send via Exponent Server SDK.
    Returns True if the push was accepted, False if the token is stale.
    Raises on server/network errors.
    """
    try:
        response = _push_client.publish(
            PushMessage(
                to=token,
                title=title,
                body=body,
                data=extra or {},
                sound="default",
            )
        )
    except PushServerError as exc:
        logger.error(f"Expo Server Error when sending push: {exc.errors}")
        raise
    except (ConnectionError, HTTPError) as exc:
        logger.error(f"Network Error when connecting to Expo: {exc}")
        raise

    try:
        response.validate_response()
        logger.info(f"Push notification sent successfully to {token[:30]}...")
        return True
    except DeviceNotRegisteredError:
        logger.warning(f"Device not registered for token: {token[:30]}... — marking as stale.")
        return False  # caller should clean up the token
    except PushTicketError as exc:
        logger.error(f"Push ticket error for {token[:30]}...: {exc.push_response}")
        return True  # token may still be valid, just a transient issue
    except Exception as exc:
        logger.error(f"Unexpected error validating push response for {token[:30]}...: {exc}")
        return True


async def send_push_notification(token: str, title: str, body: str, extra: dict | None = None) -> bool:
    """
    Async-safe wrapper: offloads the synchronous HTTP call to a thread
    so it doesn't block the asyncio event loop.
    Returns True if accepted, False if token is stale (DeviceNotRegistered).
    """
    if not token or not token.startswith("ExponentPushToken["):
        logger.warning(f"Invalid or missing Expo push token: {token}")
        return False

    return await asyncio.to_thread(_send_push_sync, token, title, body, extra)
