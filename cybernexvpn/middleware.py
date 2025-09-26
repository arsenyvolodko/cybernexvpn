import logging
import ipaddress


logger = logging.getLogger(__name__)


class YookassaCallbackMiddleware:

    def __init__(self, get_response):
        logger.info("YookassaCallbackMiddleware initialized")
        self.get_response = get_response

    @staticmethod
    def get_client_ip(request):
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        logger.info(f"HTTP_X_FORWARDED_FOR: {x_forwarded_for}")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0]
            return ipaddress.ip_address(ip)
        return request.META.get("REMOTE_ADDR")

    def __call__(self, request):
        client_ip = self.get_client_ip(request)
        logger.info(f"client_ip: {client_ip}")

        response = self.get_response(request)
        return response
