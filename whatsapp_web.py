"""Public AstrBot Web API, with the Quart boundary for supported older cores."""
try:
    from astrbot.api.web import json_response, request
except ModuleNotFoundError as exc:
    if exc.name != 'astrbot.api.web':
        raise
    import json
    from quart import Response, request as _quart_request

    class _LegacyRequest:
        async def json(self, default=None):
            value = await _quart_request.get_json(silent=True)
            return default if value is None else value

    request = _LegacyRequest()

    def json_response(data=None, *, status_code=200, headers=None):
        return Response(json.dumps({} if data is None else data, ensure_ascii=False),
                        status=status_code, headers=headers, content_type='application/json')


__all__ = ['json_response', 'request']
