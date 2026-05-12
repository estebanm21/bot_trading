"""
╔══════════════════════════════════════════════════════════════════╗
║      MASTER ESTRATEGIA BOT - MULTI-USUARIO                     ║
║      SMC + MACD + BTC  |  Hasta 50 cuentas en paralelo        ║
╚══════════════════════════════════════════════════════════════════╝

Arquitectura:
  - Un hilo principal detecta señales SMC (analisis compartido)
  - Cuando hay señal, abre orden en TODAS las cuentas activas
  - Cada cuenta tiene su propio TP/SL proporcional a su capital
  - Base de referencia: $1000 → TP +$37 | SL -$60
  - Regla de tres automatica por usuario
"""

import os, sys, time, json, csv, logging, threading
from datetime import datetime, timezone
from dotenv import load_dotenv
import pandas as pd
import ta as ta_lib
import anthropic
from pybit.unified_trading import HTTP
from supabase import create_client

load_dotenv()

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────
# CONFIGURACION GLOBAL
# ─────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "https://rhqkvmastypsithenaww.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")

SYMBOLS      = ["SOLUSDT", "XRPUSDT", "ETHUSDT"]
BTC_SYMBOL   = "BTCUSDT"
LOG_FILE     = "trades_log.csv"
CHECK_EVERY  = 60       # segundos entre scans
COMMISSION_PCT = 0.0011

# Base de referencia para calcular TP/SL proporcional
BASE_CAPITAL = 1000.0   # $1000 de referencia
BASE_TP_USD  = 37.0     # +$37 con $1000
BASE_SL_USD  = 60.0     # -$60 con $1000

sb     = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(stream=sys.stdout)
    ]
)
log = logging.getLogger(__name__)

# Lock para escritura segura en CSV (multi-hilo)
csv_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# 1. CARGAR USUARIOS ACTIVOS DESDE SUPABASE
# ═══════════════════════════════════════════════════════════════

def get_active_users() -> list:
    """
    Devuelve lista de usuarios con API keys configuradas.
    Solo usuarios que tienen bybit_api_key y bybit_api_secret.
    """
    try:
        res = sb.table("bot_config") \
                .select("user_id, bybit_api_key, bybit_api_secret, order_usdt, leverage") \
                .not_.is_("bybit_api_key", "null") \
                .not_.is_("bybit_api_secret", "null") \
                .execute()
        users = res.data or []
        log.info(f"Usuarios activos cargados: {len(users)}")
        return users
    except Exception as e:
        log.error(f"Error cargando usuarios: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# 2. ANALISIS DE MERCADO (COMPARTIDO — corre una sola vez)
# ═══════════════════════════════════════════════════════════════

def get_candles_public(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """Obtiene velas usando cliente publico (sin API key, datos de mercado)."""
    public = HTTP(testnet=False)
    for attempt in range(3):
        try:
            resp = public.get_kline(
                category="linear", symbol=symbol,
                interval=interval, limit=limit
            )
            df = pd.DataFrame(resp["result"]["list"],
                              columns=["timestamp","open","high","low","close","volume","turnover"])
            df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                log.error(f"Error velas {symbol} {interval}: {e}")
                return pd.DataFrame()
    return pd.DataFrame()


def calc_indicators(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 30:
        return {}
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    macd   = ta_lib.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    bb     = ta_lib.volatility.BollingerBands(c, window=20, window_dev=2)
    rsi    = ta_lib.momentum.RSIIndicator(c, window=14).rsi()
    ema20  = ta_lib.trend.EMAIndicator(c, window=20).ema_indicator()
    ema50  = ta_lib.trend.EMAIndicator(c, window=50).ema_indicator()
    ema200 = ta_lib.trend.EMAIndicator(c, window=min(200, len(c)-1)).ema_indicator()

    recent_high = round(float(h.tail(20).max()), 4)
    recent_low  = round(float(l.tail(20).min()), 4)

    macd_hist_now  = round(float(macd.macd_diff().iloc[-1]), 6)
    macd_hist_prev = round(float(macd.macd_diff().iloc[-2]), 6)

    if macd_hist_now > 0 and macd_hist_prev > 0:
        macd_color = "verde_fuerte" if macd_hist_now > macd_hist_prev else "verde_claro"
    elif macd_hist_now < 0 and macd_hist_prev < 0:
        macd_color = "rojo_fuerte" if abs(macd_hist_now) > abs(macd_hist_prev) else "rojo_claro"
    elif macd_hist_now > 0 and macd_hist_prev < 0:
        macd_color = "cambio_rojo_a_verde"
    elif macd_hist_now < 0 and macd_hist_prev > 0:
        macd_color = "cambio_verde_a_rojo"
    else:
        macd_color = "neutral"

    price_now = round(float(c.iloc[-1]), 6)

    if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) > float(ema200.iloc[-1]):
        trend = "alcista_fuerte"
    elif float(ema20.iloc[-1]) > float(ema50.iloc[-1]):
        trend = "alcista"
    elif float(ema20.iloc[-1]) < float(ema50.iloc[-1]) < float(ema200.iloc[-1]):
        trend = "bajista_fuerte"
    elif float(ema20.iloc[-1]) < float(ema50.iloc[-1]):
        trend = "bajista"
    else:
        trend = "lateral"

    bb_range = float(bb.bollinger_hband().iloc[-1]) - float(bb.bollinger_lband().iloc[-1])
    bb_pos   = round((price_now - float(bb.bollinger_lband().iloc[-1])) / max(bb_range, 0.0001) * 100, 1)

    return {
        "price": price_now, "trend": trend,
        "ema20": round(float(ema20.iloc[-1]), 6),
        "ema50": round(float(ema50.iloc[-1]), 6),
        "ema200": round(float(ema200.iloc[-1]), 6),
        "price_vs_ema20": "encima" if price_now > float(ema20.iloc[-1]) else "debajo",
        "price_vs_ema50": "encima" if price_now > float(ema50.iloc[-1]) else "debajo",
        "rsi": round(float(rsi.iloc[-1]), 2),
        "macd_hist": macd_hist_now, "macd_hist_prev": macd_hist_prev,
        "macd_color": macd_color,
        "bb_upper": round(float(bb.bollinger_hband().iloc[-1]), 6),
        "bb_mid":   round(float(bb.bollinger_mavg().iloc[-1]), 6),
        "bb_lower": round(float(bb.bollinger_lband().iloc[-1]), 6),
        "bb_pos": bb_pos,
        "recent_high": recent_high, "recent_low": recent_low,
        "prev_high": round(float(h.iloc[-2]), 4),
        "prev_low":  round(float(l.iloc[-2]), 4),
        "vol_now": round(float(v.iloc[-1]), 2),
        "vol_avg": round(float(v.tail(20).mean()), 2),
    }


def get_full_analysis(symbol: str) -> dict:
    data = {"symbol": symbol}
    for tf, label in [("240","4h"),("60","1h"),("30","30m"),("15","15m")]:
        df  = get_candles_public(symbol, tf, limit=100)
        ind = calc_indicators(df)
        data[label] = ind
        time.sleep(0.2)
    return data


# ═══════════════════════════════════════════════════════════════
# 3. IA — ANALISIS SMC (igual que antes, compartido)
# ═══════════════════════════════════════════════════════════════

def analyze_with_smc(all_data: list, btc_data: dict) -> dict:
    assets_text = ""
    for d in all_data:
        sym = d["symbol"]
        h4  = d.get("4h", {})
        h1  = d.get("1h", {})
        m15 = d.get("15m", {})
        m30 = d.get("30m", {})
        if not h4 or not h1 or not m15:
            continue
        assets_text += f"""
═══ {sym} ═══
4H: Tendencia={h4.get('trend')} | Precio={h4.get('price')} | RSI={h4.get('rsi')} | MACD={h4.get('macd_color')}
    Zona alta={h4.get('recent_high')} | Zona baja={h4.get('recent_low')} | BB pos={h4.get('bb_pos')}%
1H: Tendencia={h1.get('trend')} | Precio={h1.get('price')} | RSI={h1.get('rsi')} | MACD={h1.get('macd_color')}
    Zona alta={h1.get('recent_high')} | Zona baja={h1.get('recent_low')} | BB pos={h1.get('bb_pos')}%
30M: Tendencia={m30.get('trend')} | MACD={m30.get('macd_color')} | BB pos={m30.get('bb_pos')}%
15M: MACD hist={m15.get('macd_hist')} | COLOR={m15.get('macd_color')} | RSI={m15.get('rsi')} | Vol={m15.get('vol_now')}/{m15.get('vol_avg')}
"""

    btc_h4  = btc_data.get("4h", {})
    btc_h1  = btc_data.get("1h", {})
    btc_m15 = btc_data.get("15m", {})
    btc_text = f"""
BTC: 4H={btc_h4.get('trend')} MACD={btc_h4.get('macd_color')} | 1H={btc_h1.get('trend')} MACD={btc_h1.get('macd_color')} | 15M MACD={btc_m15.get('macd_color')} RSI={btc_m15.get('rsi')}
"""

    prompt = f"""Eres un trader experto en Smart Money Concept (SMC) aplicando LA MASTER ESTRATEGIA con 5 pilares.

DATOS:
{assets_text}
{btc_text}

PILARES:
1. TENDENCIA: Identifica macro (4H) y mini-tendencia (1H+30M). No necesitan ir en misma dirección.
2. ZONA SMC: Precio debe estar en soporte/resistencia clave (bb_pos<25% o >75%, cerca de recent_high/low, RSI extremo)
3. POSICIONAMIENTO: Precio tocando la zona, no en zona media
4. MACD 15M: cambio_rojo_a_verde=compra, cambio_verde_a_rojo=venta. Sin esto NO entrar.
5. BTC: alineado con la dirección de la operación

REGLAS: Mínimo 4/5 pilares. Si no hay setup claro -> NO_TRADE.

Responde SOLO este JSON:
{{
  "action": "TRADE" o "NO_TRADE",
  "symbol": "SOLUSDT/XRPUSDT/ETHUSDT",
  "signal": "UP" o "DOWN",
  "confidence": 0.0-1.0,
  "pilares_cumplidos": 1-5,
  "tendencia_macro": "...",
  "mini_tendencia": "...",
  "zona_reaccion": "...",
  "macd_confirmacion": "...",
  "btc_alineado": true/false,
  "razon": "..."
}}"""

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        if raw.count("{") > raw.count("}"):
            raw += "}" * (raw.count("{") - raw.count("}"))
        return json.loads(raw)
    except Exception as e:
        log.error(f"Error IA: {e}")
        return {"action": "NO_TRADE", "confidence": 0, "razon": f"Error IA: {e}"}


# ═══════════════════════════════════════════════════════════════
# 4. OPERACIONES POR USUARIO (cada uno con su Bybit client)
# ═══════════════════════════════════════════════════════════════

def get_price_user(client: HTTP, symbol: str) -> float:
    try:
        return float(client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
    except:
        return 0.0

def calc_tp_sl(order_usdt: float, leverage: int) -> tuple:
    """
    Regla de tres proporcional al capital del usuario.
    Base: $1000 → TP +$37 | SL -$60
    """
    ratio    = (order_usdt * leverage) / (BASE_CAPITAL * 10)  # 10 = leverage base
    tp_usd   = round(BASE_TP_USD * ratio, 2)
    sl_usd   = round(BASE_SL_USD * ratio, 2)
    comm_usd = round(order_usdt * leverage * COMMISSION_PCT, 2)
    return tp_usd, sl_usd, comm_usd

def get_qty_user(client: HTTP, symbol: str, order_usdt: float, leverage: int) -> str:
    try:
        price    = get_price_user(client, symbol)
        info     = client.get_instruments_info(category="linear", symbol=symbol)
        lot      = info["result"]["list"][0]["lotSizeFilter"]
        qty_step = float(lot.get("qtyStep") or lot.get("basePrecision") or 0.001)
        min_qty  = float(lot.get("minOrderQty") or 0.001)
        qty      = max(min_qty, round((order_usdt * leverage / price) / qty_step) * qty_step)
        return str(round(qty, 8))
    except Exception as e:
        log.error(f"Error qty: {e}")
        return "0"

def open_order_user(client: HTTP, symbol: str, side: str, qty: str, leverage: int):
    try:
        client.set_leverage(category="linear", symbol=symbol,
                            buyLeverage=str(leverage), sellLeverage=str(leverage))
    except:
        pass
    try:
        resp = client.place_order(
            category="linear", symbol=symbol,
            side=side, orderType="Market", qty=qty,
            timeInForce="IOC", reduceOnly=False
        )
        return resp["result"].get("orderId","N/A")
    except Exception as e:
        log.error(f"Error orden: {e}")
        return None

def close_order_user(client: HTTP, symbol: str) -> float:
    try:
        positions = client.get_positions(category="linear", symbol=symbol)
        pos_list  = positions["result"]["list"]
        if not pos_list or float(pos_list[0]["size"]) == 0:
            return get_price_user(client, symbol)
        pos  = pos_list[0]
        side = "Sell" if pos["side"] == "Buy" else "Buy"
        client.place_order(
            category="linear", symbol=symbol,
            side=side, orderType="Market",
            qty=pos["size"], reduceOnly=True, timeInForce="IOC"
        )
        return get_price_user(client, symbol)
    except Exception as e:
        log.error(f"Error cerrando: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════════════
# 5. LOG
# ═══════════════════════════════════════════════════════════════

def save_trade(user_id, symbol, signal, confidence, entry, close_px, order_usdt, leverage, pilares, razon):
    controlled = order_usdt * leverage
    commission = round(controlled * COMMISSION_PCT, 4)
    pnl_pct    = round((close_px-entry)/entry*100, 4) if signal=="UP" else round((entry-close_px)/entry*100, 4)
    pnl_usd    = round(controlled * pnl_pct / 100 - commission, 4)
    result     = "WIN" if pnl_usd > 0 else "LOSS"

    with csv_lock:
        exists = os.path.isfile(LOG_FILE)
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["timestamp","user_id","symbol","signal","confidence","pilares",
                            "order_usdt","leverage","entry","close","pnl_pct","pnl_usd","commission","result","razon"])
            w.writerow([
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                user_id[:8]+"...",  # parcial por privacidad
                symbol, signal, confidence, pilares,
                order_usdt, leverage, entry, close_px,
                pnl_pct, pnl_usd, commission, result, razon
            ])

    # Guardar en Supabase también
    try:
        sb.table("trades_log").insert({
            "user_id":    user_id,
            "symbol":     symbol,
            "signal":     signal,
            "confidence": confidence,
            "pilares":    pilares,
            "order_usdt": order_usdt,
            "leverage":   leverage,
            "entry":      entry,
            "close_price": close_px,
            "pnl_pct":    pnl_pct,
            "pnl_usd":    pnl_usd,
            "commission": commission,
            "result":     result,
            "razon":      razon
        }).execute()
    except Exception as e:
        log.warning(f"Error guardando trade en Supabase: {e}")

    return result, pnl_usd


# ═══════════════════════════════════════════════════════════════
# 6. HILO POR USUARIO — opera y monitorea su posición
# ═══════════════════════════════════════════════════════════════

def user_trade_thread(user: dict, decision: dict):
    """
    Corre en un hilo separado por cada usuario.
    Abre la orden, monitorea, y cierra cuando llega a TP/SL.
    """
    uid        = user["user_id"]
    api_key    = user["bybit_api_key"]
    api_secret = user["bybit_api_secret"]
    order_usdt = float(user.get("order_usdt") or 100)
    leverage   = int(user.get("leverage") or 10)

    symbol     = decision["symbol"]
    signal     = decision["signal"]
    confidence = decision["confidence"]
    pilares    = decision["pilares_cumplidos"]
    razon      = decision["razon"]
    side       = "Buy" if signal == "UP" else "Sell"

    # TP y SL proporcionales al capital del usuario
    tp_usd, sl_usd, comm = calc_tp_sl(order_usdt, leverage)
    controlled = order_usdt * leverage

    log.info(f"  [Usuario {uid[:8]}] Capital: ${order_usdt} x{leverage} = ${controlled} | TP: +${tp_usd} | SL: -${sl_usd}")

    try:
        client = HTTP(testnet=False, demo=False, api_key=api_key, api_secret=api_secret)
    except Exception as e:
        log.error(f"  [Usuario {uid[:8]}] Error creando cliente Bybit: {e}")
        return

    qty = get_qty_user(client, symbol, order_usdt, leverage)
    if qty == "0":
        log.error(f"  [Usuario {uid[:8]}] Cantidad inválida, omitiendo")
        return

    order_id = open_order_user(client, symbol, side, qty, leverage)
    if not order_id:
        log.error(f"  [Usuario {uid[:8]}] Orden fallida")
        return

    entry_px = get_price_user(client, symbol)
    log.info(f"  [Usuario {uid[:8]}] ✅ Orden abierta {signal} {symbol} @ ${entry_px} | ID: {order_id}")

    # Monitorear hasta TP/SL/timeout
    open_time = datetime.now(timezone.utc)
    while True:
        time.sleep(CHECK_EVERY)
        current_px = get_price_user(client, symbol)
        if current_px == 0:
            continue

        if signal == "UP":
            pnl = round(controlled * (current_px - entry_px) / entry_px - comm, 2)
        else:
            pnl = round(controlled * (entry_px - current_px) / entry_px - comm, 2)

        elapsed = (datetime.now(timezone.utc) - open_time).seconds / 60
        log.info(f"  [Usuario {uid[:8]}] {symbol} PnL: ${pnl:+.2f} | Precio: {current_px} | {elapsed:.0f}m")

        should_close = False
        if pnl >= tp_usd:
            should_close = True
            log.info(f"  [Usuario {uid[:8]}] 🎯 TP alcanzado: ${pnl:+.2f}")
        elif pnl <= -sl_usd:
            should_close = True
            log.info(f"  [Usuario {uid[:8]}] 🛑 SL alcanzado: ${pnl:+.2f}")
        elif elapsed >= 240:
            should_close = True
            log.info(f"  [Usuario {uid[:8]}] ⏰ Tiempo máximo: ${pnl:+.2f}")

        if should_close:
            close_px = close_order_user(client, symbol)
            result, final_pnl = save_trade(
                uid, symbol, signal, confidence, entry_px, close_px,
                order_usdt, leverage, pilares, razon
            )
            log.info(f"  [Usuario {uid[:8]}] {'✅ WIN' if result=='WIN' else '❌ LOSS'} | PnL final: ${final_pnl:+.2f}")
            break


# ═══════════════════════════════════════════════════════════════
# 7. BUCLE PRINCIPAL
# ═══════════════════════════════════════════════════════════════

def run():
    log.info("=" * 60)
    log.info("  MASTER ESTRATEGIA BOT — MODO MULTI-USUARIO INICIADO")
    log.info("=" * 60)

    scan_count = 0

    while True:
        scan_count += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        log.info(f"\n[{ts}] SCAN #{scan_count} — Analizando mercado...")

        # 1. Cargar usuarios activos
        users = get_active_users()
        if not users:
            log.warning("  Sin usuarios activos. Esperando...")
            time.sleep(CHECK_EVERY)
            continue

        log.info(f"  {len(users)} usuario(s) activo(s) en este ciclo")

        # 2. Analisis de mercado compartido (una sola vez para todos)
        btc_data = get_full_analysis(BTC_SYMBOL)
        all_data = []
        for sym in SYMBOLS:
            data = get_full_analysis(sym)
            all_data.append(data)
            time.sleep(0.3)

        # 3. IA decide si hay señal
        decision = analyze_with_smc(all_data, btc_data)
        action   = decision.get("action", "NO_TRADE")
        pilares  = decision.get("pilares_cumplidos", 0)

        log.info(f"  Decisión IA: {action} | Pilares: {pilares}/5 | Confianza: {decision.get('confidence', 0)}")
        log.info(f"  Razón: {decision.get('razon', '')}")

        # 4. Si hay señal válida, lanzar hilo por cada usuario
        if action == "TRADE" and pilares >= 4:
            symbol = decision.get("symbol", SYMBOLS[0])
            signal = decision.get("signal", "UP")
            log.info(f"\n  🚀 SEÑAL DETECTADA: {signal} {symbol}")
            log.info(f"  Abriendo órdenes para {len(users)} usuario(s)...\n")

            threads = []
            for user in users:
                t = threading.Thread(
                    target=user_trade_thread,
                    args=(user, decision),
                    daemon=True,
                    name=f"user-{user['user_id'][:8]}"
                )
                t.start()
                threads.append(t)
                time.sleep(0.5)  # pequeño delay entre usuarios para no saturar

            log.info(f"  {len(threads)} hilo(s) de trading iniciados")

            # Esperar a que todos cierren sus posiciones
            for t in threads:
                t.join()

            log.info("  Todos los usuarios cerraron sus posiciones en este ciclo.")

        else:
            if action == "NO_TRADE":
                log.info("  Sin setup claro. Esperando próxima oportunidad...")
            else:
                log.info(f"  Pilares insuficientes ({pilares}/5). Se requieren mínimo 4.")

        log.info(f"  Próximo scan en {CHECK_EVERY}s...")
        time.sleep(CHECK_EVERY)


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    missing = []
    if not SUPABASE_URL:        missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_KEY: missing.append("SUPABASE_SERVICE_KEY")
    if not ANTHROPIC_API_KEY:   missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"ERROR: Faltan variables de entorno: {', '.join(missing)}")
        exit(1)
    run()