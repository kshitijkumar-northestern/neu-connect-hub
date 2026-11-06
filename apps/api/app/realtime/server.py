import socketio

from app.core.config import Settings


def create_socketio_server(settings: Settings) -> socketio.AsyncServer:
    """Socket.IO server that only accepts browser connections from the web origin.

    Session authentication on connect comes with the auth module; no events are registered yet.
    """
    return socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=[settings.web_origin],
        logger=False,
        engineio_logger=False,
    )
