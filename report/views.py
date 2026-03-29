from django.shortcuts import render
# from .db_service import get_daily_data
from .ai_service import generate_report
import re, os, sqlite3
from django.http import JsonResponse
from .models import User
from django.conf import settings
import logging
logger = logging.getLogger(__name__)


def autocomplete_name(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse([], safe=False)

    db_path = os.path.join(settings.BASE_DIR, "db", "protectee.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # users 테이블에서 이름 검색
    cursor.execute("SELECT name FROM users WHERE name LIKE ? ORDER BY name LIMIT 10", (f"%{query}%",))
    rows = cursor.fetchall()
    names = [row[0] for row in rows]

    conn.close()
    return JsonResponse(names, safe=False)

def report(request):
    name = request.GET.get("name")
    date = request.GET.get("period")

    report_text = None
    if name and date:
        report_text = generate_report(name, date)

    return render(request, "report.html", {"report": report_text})

