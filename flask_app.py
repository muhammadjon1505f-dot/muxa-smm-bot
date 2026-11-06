"""
PythonAnywhere Bepul Tarif (Free Tier) uchun Flask WSGI Webhook Server.
Ushbu fayl PythonAnywhere'da botni 24/7 uzluksiz va maksimal tezlikda ishlashini ta'minlaydi.
"""
import os
import sys
import asyncio
import threading
from flask import Flask, request, jsonify, abort
from aiogram.types import Update

# Loyiha papkasini qidiruv yo'liga qo'shish
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# bot.py modulini yuklash
from bot import bot, dp, BOT_TOKEN, set_main_menu

app = Flask(__name__)

# Doimiy background event loop — ulanishlarni qayta ishlatish orqali maksimal tezlik ta'minlanadi
_loop = None
_thread = None
_lock = threading.Lock()


def get_worker_loop():
    global _loop, _thread
    if _loop is None or _thread is None or not _thread.is_alive():
        with _lock:
            if _loop is None or _thread is None or not _thread.is_alive():
                _loop = asyncio.new_event_loop()
                _thread = threading.Thread(target=_loop.run_forever, daemon=True)
                _thread.start()
    return _loop


async def _handle_update_async(data: dict):
    try:
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        app.logger.error(f"Update qayta ishlashda xatolik: {e}")
        if hasattr(bot, "session") and hasattr(bot.session, "_session"):
            try:
                if bot.session._session and not bot.session._session.closed:
                    await bot.session._session.close()
            except Exception:
                pass
            bot.session._session = None


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.headers.get("content-type", "").startswith("application/json"):
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"ok": True}), 200

        try:
            loop = get_worker_loop()
            future = asyncio.run_coroutine_threadsafe(_handle_update_async(data), loop)
            future.result(timeout=12)
        except Exception as e:
            app.logger.error(f"Update timeout yoki xato: {e}")
            return jsonify({"ok": True, "handled_error": str(e)}), 200

        return jsonify({"ok": True}), 200
    abort(403)


@app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    host = request.host_url.rstrip("/")
    if host.startswith("http://"):
        host = "https://" + host[7:]
    webhook_url = f"{host}/{BOT_TOKEN}"

    async def _setup():
        try:
            await set_main_menu(bot)
        except Exception:
            pass
        for attempt in range(3):
            try:
                result = await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
                info = await bot.get_webhook_info()
                return result, info
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(0.5)
                else:
                    raise

    try:
        loop = get_worker_loop()
        future = asyncio.run_coroutine_threadsafe(_setup(), loop)
        result, info = future.result(timeout=15)
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
        for attempt in range(3):
            try:
                return await bot.get_webhook_info()
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(0.5)
                else:
                    raise

    try:
        loop = get_worker_loop()
        future = asyncio.run_coroutine_threadsafe(_info(), loop)
        info = future.result(timeout=15)
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
            <h1>🤖 Muxa SMM Bot (High-Speed 24/7 Webhook)</h1>
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
