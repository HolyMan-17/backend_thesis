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

def send_push_notification(token: str, title: str, body: str, extra: dict = None):
    """
    Sincrónicamente envía un push notification usando Exponent Server SDK.
    Si bien es sincrónico, se sugiere usar background tasks en FastAPI.
    """
    if not token or not token.startswith("ExponentPushToken["):
        logger.warning(f"Invalid or missing Expo push token: {token}")
        return

    try:
        response = PushClient().publish(
            PushMessage(
                to=token,
                title=title,
                body=body,
                data=extra or {},
                sound="default"
            )
        )
    except PushServerError as exc:
        logger.error(f"Expo Server Error when sending push: {exc.errors}")
        raise
    except (ConnectionError, HTTPError) as exc:
        logger.error(f"Network Error when connecting to Expo: {exc}")
        raise

    try:
        # Verifica errores en la respuesta de Expo (ej. DeviceNotRegistered)
        response.validate_response()
        logger.info(f"Push notification sent successfully to {token}")
    except DeviceNotRegisteredError:
        logger.warning(f"Device not registered for token: {token}. Token might be stale.")
        # Aquí podrías borrar el token de la DB si lo pasas como dependencia
    except PushTicketError as exc:
        logger.error(f"Push ticket error for {token}: {exc.push_response.dict()}")
    except Exception as exc:
        logger.error(f"Unexpected error sending push to {token}: {exc}")
