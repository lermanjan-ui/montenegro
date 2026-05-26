from django.http import HttpResponse


class PublicCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin")

        allowed_origins = {
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://raccoon.uz",
            "https://www.raccoon.uz",
        }

        is_public_api = request.path.startswith("/api/public/")

        if is_public_api and request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        if is_public_api and origin in allowed_origins:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = (
                "accept, authorization, content-type, user-agent, "
                "x-csrftoken, x-requested-with"
            )
            response["Access-Control-Max-Age"] = "86400"

        return response