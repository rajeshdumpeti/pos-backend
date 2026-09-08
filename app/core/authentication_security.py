"""ASGI authentication HTTP bounds, trusted source resolution and admission."""
import ipaddress
import json
import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Scope, Receive, Send

from app.config import Settings
from app.application.email_registration import AuthenticationEventLogger

from app.api.errors import _error_response
from app.application.authentication_security import AuthenticationAdmission, LimiterUnavailableError
from app.core.request_id import get_request_id


OPERATIONS = {
    ('GET', '/registration-policy'): ('registration_policy', None, False),
    ('POST', '/registrations'): ('registration', 'email', True),
    ('POST', '/email-verifications/confirm'): ('verification_confirm', 'flow_token', False),
    ('POST', '/email-verifications/resend'): ('verification_resend', 'flow_token', False),
    ('PATCH', '/email-verifications/email'): ('change_email', 'new_email', True),
    ('POST', '/sessions'): ('sign_in', 'email', True),
    ('POST', '/password-resets'): ('password_reset_request', 'email', True),
    ('POST', '/password-resets/confirm'): ('password_reset_confirm', 'token', False),
    ('POST', '/sessions/refresh'): ('refresh', 'refresh_token', False),
    ('GET', '/session'): ('restore', None, False),
    ('DELETE', '/session'): ('logout', None, False),
    ('DELETE', '/sessions'): ('logout', None, False),
}
_BEARER = re.compile(r'Bearer [A-Za-z0-9._~+/-]+=*', re.IGNORECASE)


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if '%' in value:
        raise ValueError('Invalid source')
    address = ipaddress.ip_address(value)
    return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address


class TrustedClientSource:
    """Walk XFF right-to-left from a configured trusted immediate peer."""
    def __init__(self, networks: tuple[str, ...] = ()) -> None:
        self._networks = tuple(ipaddress.ip_network(n) for n in networks)

    def resolve(self, peer: str, forwarded: list[str], standard: list[str]) -> str:
        try:
            immediate = _address(peer)
        except ValueError:
            # ASGI test clients/Unix sockets have no network address: one bucket.
            return 'local-unknown-source'
        if not any(immediate in n for n in self._networks):
            return str(immediate)
        if standard or len(forwarded) > 1:
            raise ValueError('Ambiguous source')
        if not forwarded:
            return str(immediate)
        if len(forwarded[0]) > 1024:
            raise ValueError('Invalid source')
        parts = forwarded[0].split(',')
        if not 1 <= len(parts) <= 8:
            raise ValueError('Invalid source')
        chain = [_address(part.strip()) for part in parts]
        current = immediate
        for address in reversed(chain):
            if not any(current in n for n in self._networks):
                break
            current = address
        return str(current)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Ambiguous JSON')
        result[key] = value
    return result


class AuthenticationSecurityMiddleware:
    def __init__(self, app: ASGIApp, *, admission: AuthenticationAdmission,
                 settings: Settings, diagnostic: AuthenticationEventLogger) -> None:
        self.app = app
        self.admission = admission
        self.settings = settings
        self.diagnostic = diagnostic
        self.sources = TrustedClientSource(settings.auth_trusted_proxy_networks)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not scope['path'].startswith('/api/v1/auth'):
            return await self.app(scope, receive, send)
        request = Request(scope)
        operation, identity_field, is_email = OPERATIONS.get(
            (scope['method'], scope['path'][12:].rstrip('/')), ('restore', None, False))

        async def safe_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message['headers']
                           if k.lower() not in {b'cache-control', b'pragma', b'server', b'x-powered-by'}]
                headers.extend([(b'cache-control', b'no-store'), (b'pragma', b'no-cache')])
                message = {**message, 'headers': headers}
            await send(message)

        async def reject(status, code, message, retry=0):
            response = _error_response(request, status, code, message,
                extra_headers={'Retry-After': str(retry)} if retry else None)
            await response(scope, receive, safe_send)

        async def admission_result(call):
            try:
                retry = await run_in_threadpool(call)
                category = 'rate_limited' if retry else None
            except LimiterUnavailableError:
                retry, category = 0, 'dependency_unavailable'
            if category is None:
                return False
            self.diagnostic.emit(event='authentication_admission_rejected', operation=operation,
                outcome='suppressed' if operation == 'password_reset_request' else 'rejected',
                request_id=get_request_id(request), error_category=category)
            if operation == 'password_reset_request':
                response = JSONResponse({'status': 'instructions_if_eligible'}, status_code=202)
                await response(scope, receive, safe_send)
            else:
                await reject(429 if retry else 503, category,
                    'Please try again later' if retry else 'A required dependency is unavailable', retry)
            return True

        try:
            source = self.sources.resolve(request.client.host if request.client else '',
                request.headers.getlist('x-forwarded-for'), request.headers.getlist('forwarded'))
        except ValueError:
            return await reject(400, 'request_validation_failed', 'Request validation failed')
        if await admission_result(lambda: self.admission.check(operation, source)):
            return
        # No auth endpoint accepts credentials in URLs. Reject all query input,
        # including unknown token aliases, without echoing names or values.
        if scope.get('query_string'):
            return await reject(400, 'request_validation_failed', 'Request validation failed')
        credentials = request.headers.getlist('authorization')
        if credentials and (len(credentials) != 1
                or len(credentials[0]) > self.settings.auth_max_authorization_bytes
                or not _BEARER.fullmatch(credentials[0])):
            return await reject(401, 'authentication_required', 'Authentication is required')
        retry_keys = request.headers.getlist('idempotency-key')
        if retry_keys and (len(retry_keys) != 1 or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', retry_keys[0])):
            return await reject(422, 'request_validation_failed', 'Request validation failed')
        lengths = request.headers.getlist('content-length')
        if lengths and request.headers.get('transfer-encoding'):
            return await reject(400, 'request_validation_failed', 'Request validation failed')
        if lengths and (len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,8}', lengths[0])):
            return await reject(400, 'request_validation_failed', 'Request validation failed')
        if lengths and int(lengths[0]) > self.settings.auth_max_body_bytes:
            return await reject(413, 'request_too_large', 'Request body is too large')
        if request.headers.get('content-encoding', 'identity') != 'identity':
            return await reject(415, 'unsupported_media_type', 'JSON content is required')
        if scope['method'] in {'POST', 'PATCH'}:
            types = request.headers.getlist('content-type')
            if len(types) != 1 or types[0].split(';')[0].strip().lower() != 'application/json':
                return await reject(415, 'unsupported_media_type', 'JSON content is required')
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > self.settings.auth_max_body_bytes:
                return await reject(413, 'request_too_large', 'Request body is too large')
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        if body:
            try:
                payload = json.loads(body, object_pairs_hook=_unique_object,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(payload, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                return await reject(422, 'request_validation_failed', 'Request validation failed')
            identity = payload.get(identity_field) if identity_field else None
            if isinstance(identity, str) and len(identity) <= 320:
                if await admission_result(lambda: self.admission.check_identity(operation, identity, email=is_email)):
                    return
        sent = False
        async def replay_body():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        # Catch unexpected auth failures here so ASGI servers never log exception
        # chains containing submitted input. Only a fixed diagnostic is emitted.
        try:
            await self.app(scope, replay_body, safe_send)
        except Exception:
            self.diagnostic.emit(event='authentication_internal_failure', operation=operation,
                outcome='unavailable', request_id=get_request_id(request), error_category='internal_error')
            await reject(500, 'internal_server_error', 'An unexpected error occurred')
