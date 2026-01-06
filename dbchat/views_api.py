import json
import requests
from django.http import JsonResponse, StreamingHttpResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

LLM_BASE_URL = "http://localhost:8001/api"


@csrf_exempt
def api_ask(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    try:
        r = requests.post(
            f"{LLM_BASE_URL}/ask",
            json=body,
            timeout=30,
        )
        return JsonResponse(r.json(), status=r.status_code)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
def api_ask_stream(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        body = {}

    try:
        r = requests.post(
            f"{LLM_BASE_URL}/ask_stream",
            json=body,
            stream=True,
            timeout=60,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

    def gen():
        for chunk in r.iter_content(chunk_size=None):
            if chunk:
                yield chunk.decode("utf-8")

    return StreamingHttpResponse(
        gen(),
        content_type="text/plain; charset=utf-8",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
