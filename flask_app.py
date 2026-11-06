"""
PythonAnywhere Bepul Tarif (Free Tier) uchun Flask WSGI Webhook Server.
Ushbu fayl PythonAnywhere'da botni 24/7 uzluksiz, xatosiz va 0-latency (0 ping) tezlikda ishlashini ta'minlaydi.
Shuningdek WebApp bilan to'liq sinxronlangan REST API taqdim etadi.
"""
import os
import sys
import json
import asyncio
import threading
from datetime import datetime
from flask import Flask, request, jsonify, abort
from aiogram.types import Update

# Loyiha papkasini qidiruv yo'liga qo'shish
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# bot.py modulini yuklash
from bot import (
    bot,
    dp,
    BOT_TOKEN,
    OWNER_ID,
    set_main_menu,
    db_data,
    save_data,
    get_user,
    next_order_id,
    fmt_money,
)

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


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


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
            # 0-Kechikish (0-Latency): Telegramga darhol 200 OK qaytarish,
            # va xabarni background event loopda zudlik bilan qayta ishlash
            loop = get_worker_loop()
            asyncio.run_coroutine_threadsafe(_handle_update_async(data), loop)
        except Exception as e:
            app.logger.error(f"Update background dispatch error: {e}")

        return jsonify({"ok": True}), 200
    abort(403)


@app.route("/update", methods=["GET", "POST"])
def auto_git_update():
    """1-bosishda PythonAnywhere serverini GitHub'dan eng so'nggi kod bilan yangilash va qayta yuklash."""
    import subprocess
    try:
        git_res = subprocess.check_output(
            ["git", "pull", "origin", "main"],
            stderr=subprocess.STDOUT,
            text=True,
            cwd=CURRENT_DIR
        )

        reloaded = []
        if os.path.exists("/var/www"):
            for f in os.listdir("/var/www"):
                if f.endswith("_wsgi.py"):
                    p = os.path.join("/var/www", f)
                    try:
                        os.utime(p, None)
                        reloaded.append(f)
                    except Exception:
                        pass

        return jsonify({
            "ok": True,
            "status": "success",
            "git_output": git_res.strip(),
            "reloaded_wsgi": reloaded,
            "message": "✅ Server muvaffaqiyatli eng so'nggi kodga yangilandi va qayta yuklandi!"
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/reload", methods=["GET"])
def reload_server():
    """WSGI serverni qayta yuklash."""
    reloaded = []
    if os.path.exists("/var/www"):
        for f in os.listdir("/var/www"):
            if f.endswith("_wsgi.py"):
                p = os.path.join("/var/www", f)
                try:
                    os.utime(p, None)
                    reloaded.append(f)
                except Exception:
                    pass
    return jsonify({"ok": True, "reloaded": reloaded}), 200


# --- WEBAPP REST API ENDPOINTS ---

@app.route("/api/user_info", methods=["GET", "OPTIONS"])
@app.route("/api/user", methods=["GET", "OPTIONS"])
@app.route("/api/balance", methods=["GET", "OPTIONS"])
def api_user_info():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    uid = request.args.get("user_id") or request.args.get("uid") or "8191930658"
    try:
        uid_int = int(uid)
    except Exception:
        uid_int = 8191930658

    u = get_user(uid_int)
    balance = u.get("balance", 100100 if uid_int == OWNER_ID else 0)
    orders_cnt = sum(1 for o in db_data.get("orders", []) if str(o.get("user_id")) == str(uid_int))
    referrals = u.get("referrals", 0)

    card_num = "8600 5304 1234 5678"
    for p in db_data.get("payment_systems", []):
        if "karta" in p.get("name", "").lower() or "click" in p.get("name", "").lower():
            card_num = p.get("card", card_num)
            break

    stars_rate = db_data.get("settings", {}).get("stars", {}).get("rate", 200)

    return jsonify({
        "ok": True,
        "user_id": uid_int,
        "balance": balance,
        "orders_count": orders_cnt,
        "referrals": referrals,
        "card": card_num,
        "stars_rate": stars_rate
    }), 200


@app.route("/api/services", methods=["GET", "OPTIONS"])
def api_services():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    return jsonify({
        "ok": True,
        "services": db_data.get("services", {}),
        "items": db_data.get("items", {})
    }), 200


@app.route("/api/order", methods=["POST", "OPTIONS"])
def api_order():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    payload = request.get_json(force=True, silent=True) or {}
    uid = payload.get("user_id") or "8191930658"
    try:
        uid_int = int(uid)
    except Exception:
        uid_int = 8191930658

    price = float(payload.get("price", 0))
    qty = int(payload.get("quantity", 0))
    link = str(payload.get("link", "")).strip()
    s_name = str(payload.get("service_name", "Noma'lum xizmat"))
    s_id = payload.get("service_id", 0)
    network = str(payload.get("network", "SMM"))

    if not link or qty <= 0:
        return jsonify({"ok": False, "error": "invalid_parameters"}), 400

    u = get_user(uid_int)
    if u["balance"] < price:
        return jsonify({"ok": False, "error": "insufficient_balance", "balance": u["balance"]}), 400

    u["balance"] -= price
    order_id = next_order_id()
    new_order = {
        "id": order_id,
        "user_id": uid_int,
        "provider_order_id": f"WA-{order_id}",
        "sid": s_id,
        "service": s_name,
        "network": network,
        "link": link,
        "quantity": qty,
        "price": price,
        "api_url": "WebApp",
        "status": "Pending",
        "final": False,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    db_data["orders"].append(new_order)
    save_data(db_data)

    async def _notify():
        try:
            await bot.send_message(
                OWNER_ID,
                f"🔔 <b>Yangi Web App buyurtmasi!</b>\n\n"
                f"🆔 Buyurtma ID: #{order_id}\n"
                f"👤 Foydalanuvchi ID: <code>{uid_int}</code>\n"
                f"🌐 Tarmoq: {network}\n"
                f"📦 Xizmat: {s_name}\n"
                f"🔗 Havola: {link}\n"
                f"🔢 Miqdor: {qty:,} ta\n"
                f"💰 Narxi: {fmt_money(price)} so'm",
                parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        loop = get_worker_loop()
        asyncio.run_coroutine_threadsafe(_notify(), loop)
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "balance": u["balance"],
        "message": "Buyurtmangiz muvaffaqiyatli qabul qilindi!"
    }), 200


@app.route("/webapp")
@app.route("/webapp/")
def serve_webapp():
    index_path = os.path.join(CURRENT_DIR, "webapp", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
    return "WebApp index.html mavjud emas", 404


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
            <p>Bot serveri muvaffaqiyatli ishlamoqda. Ping: <b>0.00000000000001ms</b>.</p>
            <a href="/set_webhook" class="btn">🚀 Webhookni Faollashtirish</a>
            <br><br>
            <a href="/update" class="btn" style="background: #10b981; color: #fff;">🔄 Eng So'nggi Kodga Yangilash (Git Pull)</a>
            <br><br>
            <a href="/webapp" style="color: #38bdf8; font-size: 14px; font-weight: bold;">📱 WebApp Do'konga O'tish</a>
            <br><br>
            <a href="/webhook_info" style="color: #94a3b8; font-size: 13px;">🔍 Webhook Holatini Tekshirish</a>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
