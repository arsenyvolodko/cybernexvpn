import logging

from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def logging_exception_handler(exc, context):
    response = exception_handler(exc, context)

    request = context.get("request")
    view = context.get("view")
    method = getattr(request, "method", "?")
    path = getattr(request, "path", "?")
    view_name = view.__class__.__name__ if view else "?"

    if response is None:
        logger.exception(
            "API %s %s unhandled exception in %s",
            method, path, view_name,
        )
        return response

    if response.status_code >= 500:
        logger.error(
            "API %s %s -> %s [%s]: %s",
            method, path, response.status_code, view_name, response.data,
            exc_info=True,
        )
    elif response.status_code >= 400:
        logger.warning(
            "API %s %s -> %s [%s]: %s",
            method, path, response.status_code, view_name, response.data,
        )

    return response