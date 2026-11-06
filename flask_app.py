"""
PythonAnywhere Bepul Tarif (Free Tier) uchun Flask WSGI Webhook Server.
Ushbu fayl PythonAnywhere'da botni 24/7 uzluksiz ishlashini ta'minlaydi.
"""
import os
import sys
import asyncio
from flask import Flask, request, jsonify, abort
from aiogram.types import Update

# Loyiha papkasini qidiruv yo'liga qo'shish
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# bot.py modulini yuklash
from bot import bot, dp, BOT_TOKEN, set_main_menu

app = Flask(__name__)


async def _handle_update_async(data: dict):
    try:
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    finally:
        # Har bir so'rovdan keyin aiohttp sessiyasini yopish (Event loop xatolarining oldini oladi)
        if hasattr(bot, "session") and bot.session:
            await bot.session.close()


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.headers.get("content-type", "").startswith("application/json"):
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"ok": False, "error": "Bo'sh JSON"}), 400

        try:
            asyncio.run(_handle_update_async(data))
        except Exception as e:
            app.logger.error(f"Update qayta ishlashda xatolik: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

        return jsonify({"ok": True}), 200
    abort(403)


@app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    # Telegramga webhook manzilini avtomatik ro'yxatdan o'tkazish
    host = request.host_url.rstrip("/")
    # PythonAnywhere har doim https ishlatadi
    if host.startswith("http://"):
        host = "https://" + host[7:]
    webhook_url = f"{host}/{BOT_TOKEN}"

    async def _setup():
        try:
            await set_main_menu(bot)
        except Exception:
            pass
        result = await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        info = await bot.get_webhook_info()
        await bot.session.close()
        return result, info

    try:
        result, info = asyncio.run(_setup())
        return jsonify({
            "status": "success" if result else "failed",
            "webhook_url": webhook_url,
            "pending_updates": info.pending_update_count,
            "last_error_message": info.last_error_message or "Xatolik yo'q"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/webhook_info", methods=["GET"])
def webhook_info_route():
    async def _info():
        info = await bot.get_webhook_info()
        await bot.session.close()
        return info

    try:
        info = asyncio.run(_info())
        return jsonify({
            "url": info.url,
            "has_custom_certificate": info.has_custom_certificate,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/")
def index():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Muxa SMM Bot — PythonAnywhere</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; text-align: center; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 30px; max-width: 600px; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
            h1 {{ color: #38bdf8; margin-top: 0; }}
            .btn {{ display: inline-block; background: #38bdf8; color: #000; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; margin-top: 15px; }}
            .btn:hover {{ background: #7dd3fc; }}
            p {{ line-height: 1.6; opacity: 0.9; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🤖 Muxa SMM Bot (24/7 Webhook)</h1>
            <p>Bot PythonAnywhere Free Hosting serverida muvaffaqiyatli ishga tushirildi.</p>
            <p>Agar bot xabarlarga javob bermayotgan bo'lsa, quyidagi tugmani bosib Webhookni bir marta faollashtiring:</p>
            <a href="/set_webhook" class="btn">🚀 Webhookni Faollashtirish</a>
            <br><br>
            <a href="/webhook_info" style="color: #94a3b8; font-size: 13px;">🔍 Webhook Holatini Tekshirish</a>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
