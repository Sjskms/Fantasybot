# web_admin.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from database import Database
from services.config_service import read_full_config

web_admin_app = FastAPI(title="Fantasy Admin Panel")

@web_admin_app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = read_full_config()
    total_users = await Database.get_total_users()
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Fantasy Bot Web Admin</title>
        <style>
            body {{ font-family: sans-serif; background: #121212; color: #fff; padding: 20px; }}
            .card {{ background: #1e1e1e; padding: 20px; border-radius: 8px; max-width: 600px; margin-bottom: 20px; }}
            h2 {{ color: #4CAF50; }}
            code {{ background: #2d2d2d; padding: 4px 8px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <h1>👑 Панель управления Fantasy Bot</h1>
        <div class="card">
            <h2>📊 Статистика</h2>
            <p>Всего пользователей в базе: <b>{total_users}</b></p>
        </div>
        <div class="card">
            <h2>⚙️ Системные лимиты</h2>
            <p>Сессий (Free): <code>{config.get('max_sessions_per_user', 1)}</code></p>
            <p>Сессий (Premium): <code>{config.get('premium_max_sessions_per_user', 10)}</code></p>
            <p>Каналов экспорта (Free): <code>{config.get('max_export_channels_per_session', 10)}</code></p>
            <p>Каналов экспорта (Premium): <code>{config.get('premium_max_export_channels_per_session', 100)}</code></p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)