"""
╔══════════════════════════════════════════════════════════════════╗
║         MASTER ESTRATEGIA BOT - SMC + MACD + BTC               ║
║         Multi-usuario con Supabase  |  Hasta 50 cuentas        ║
╚══════════════════════════════════════════════════════════════════╝

5 PILARES:
  1. Tendencia (4H + 1H)
  2. Zona de reaccion SMC (soporte/resistencia clave)
  3. Posicionamiento (precio dentro de la zona)
  4. Confirmacion MACD 15M (cambio de histograma)
  5. Acompanamiento BTC (alineacion de mercado)

Arquitectura multi-usuario:
  - Un hilo principal detecta señales SMC (analisis compartido)
  - Cuando hay señal, abre orden en TODAS las cuentas activas
  - Cada cuenta tiene su propio TP/SL proporcional a su capital
  - Capital y leverage se leen desde Supabase (interfaz grafica)

Regla de oro: Si no hay setup claro -> NO opera. Paciencia.
"""

import os, sys, time, json, csv, logging, threading
from datetime import datetime, timezone
from dotenv import load_dotenv
import pandas as pd
import ta as ta_lib
import anthropic
from pybit.unified_trading import HTTP
from supabase import create_client

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# CONFIGURACION GLOBAL
# ─────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "https://rhqkvmastypsithenaww.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")

SYMBOLS        = ["SOLUSDT", "XRPUSDT", "ETHUSDT"]
BTC_SYMBOL     = "BTCUSDT"
LOG_FILE       = "trades_log.csv"
CHECK_EVERY    = 60       # segundos entre scans
COMMISSION_PCT = 0.0011   # 0.055% entrada + 0.055% salida

# Base de referencia para calcular TP/SL proporcional
# Con $1000 capital y 10x leverage → TP +$37 | SL -$60
BASE_CAPITAL = 1000.0
BASE_LEVERAGE = 10
BASE_TP_USD  = 37.0
BASE_SL_USD  = 60.0

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
    order_usdt y leverage se leen desde la interfaz grafica (Supabase).
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
# 2. OBTENER VELAS MULTI-TEMPORAL
# ═══════════════════════════════════════════════════════════════

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Obtiene velas usando cliente publico (sin API key, datos de mercado).
    Incluye retry automatico igual que el bot original.
    """
    public = HTTP(testnet=False)
    interval_map = {"5": "5", "15": "15", "30": "30", "60": "60", "240": "240"}
    for attempt in range(3):
        try:
            resp = public.get_kline(
                category="linear", symbol=symbol,
                interval=interval_map.get(interval, interval), limit=limit
            )
            df = pd.DataFrame(resp["result"]["list"],
                              columns=["timestamp","open","high","low","close","volume","turnover"])
            df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            if attempt < 2:
                log.warning(f"Reintento {attempt+1} velas {symbol} {interval}m: {e}")
                time.sleep(2)
            else:
                log.error(f"Error velas {symbol} {interval}m: {e}")
                return pd.DataFrame()
    return pd.DataFrame()

def get_price(client: HTTP, symbol: str) -> float:
    try:
        return float(client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
    except:
        return 0.0


# ═══════════════════════════════════════════════════════════════
# 3. CALCULAR INDICADORES POR TEMPORALIDAD
# ═══════════════════════════════════════════════════════════════

def calc_indicators(df: pd.DataFrame) -> dict:
    """Calcula todos los indicadores para una temporalidad. Identico al bot original."""
    if df.empty or len(df) < 30:
        return {}
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    macd     = ta_lib.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    bb       = ta_lib.volatility.BollingerBands(c, window=20, window_dev=2)
    rsi      = ta_lib.momentum.RSIIndicator(c, window=14).rsi()
    ema20    = ta_lib.trend.EMAIndicator(c, window=20).ema_indicator()
    ema50    = ta_lib.trend.EMAIndicator(c, window=50).ema_indicator()
    ema200   = ta_lib.trend.EMAIndicator(c, window=min(200, len(c)-1)).ema_indicator()

    # Highs y lows recientes para zonas SMC
    recent_high = round(float(h.tail(20).max()), 4)
    recent_low  = round(float(l.tail(20).min()), 4)
    prev_high   = round(float(h.iloc[-2]), 4)
    prev_low    = round(float(l.iloc[-2]), 4)

    # MACD histograma actual y anterior (clave para confirmacion)
    macd_hist_now  = round(float(macd.macd_diff().iloc[-1]), 6)
    macd_hist_prev = round(float(macd.macd_diff().iloc[-2]), 6)

    # Determinar color del histograma MACD
    if macd_hist_now > 0 and macd_hist_prev > 0:
        macd_color = "verde_fuerte" if macd_hist_now > macd_hist_prev else "verde_claro"
    elif macd_hist_now < 0 and macd_hist_prev < 0:
        macd_color = "rojo_fuerte" if abs(macd_hist_now) > abs(macd_hist_prev) else "rojo_claro"
    elif macd_hist_now > 0 and macd_hist_prev < 0:
        macd_color = "cambio_rojo_a_verde"  # SEÑAL DE COMPRA
    elif macd_hist_now < 0 and macd_hist_prev > 0:
        macd_color = "cambio_verde_a_rojo"  # SEÑAL DE VENTA
    else:
        macd_color = "neutral"

    price_now = round(float(c.iloc[-1]), 6)

    # Tendencia basada en EMAs
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

    # Posicion del precio respecto a EMAs
    price_vs_ema20 = "encima" if price_now > float(ema20.iloc[-1]) else "debajo"
    price_vs_ema50 = "encima" if price_now > float(ema50.iloc[-1]) else "debajo"

    # Bollinger position (0=inferior, 100=superior)
    bb_range = float(bb.bollinger_hband().iloc[-1]) - float(bb.bollinger_lband().iloc[-1])
    bb_pos   = round((price_now - float(bb.bollinger_lband().iloc[-1])) / max(bb_range, 0.0001) * 100, 1)

    return {
        "price":          price_now,
        "trend":          trend,
        "ema20":          round(float(ema20.iloc[-1]), 6),
        "ema50":          round(float(ema50.iloc[-1]), 6),
        "ema200":         round(float(ema200.iloc[-1]), 6),
        "price_vs_ema20": price_vs_ema20,
        "price_vs_ema50": price_vs_ema50,
        "rsi":            round(float(rsi.iloc[-1]), 2),
        "macd_hist":      macd_hist_now,
        "macd_hist_prev": macd_hist_prev,
        "macd_color":     macd_color,
        "bb_upper":       round(float(bb.bollinger_hband().iloc[-1]), 6),
        "bb_mid":         round(float(bb.bollinger_mavg().iloc[-1]), 6),
        "bb_lower":       round(float(bb.bollinger_lband().iloc[-1]), 6),
        "bb_pos":         bb_pos,
        "recent_high":    recent_high,
        "recent_low":     recent_low,
        "prev_high":      prev_high,
        "prev_low":       prev_low,
        "vol_now":        round(float(v.iloc[-1]), 2),
        "vol_avg":        round(float(v.tail(20).mean()), 2),
    }


def get_full_analysis(symbol: str) -> dict:
    """Obtiene analisis completo multi-temporal para un simbolo. Identico al bot original."""
    data = {"symbol": symbol}
    for tf, label in [("240", "4h"), ("60", "1h"), ("30", "30m"), ("15", "15m")]:
        df  = get_candles(symbol, tf, limit=100)
        ind = calc_indicators(df)
        data[label] = ind
        time.sleep(0.2)
    return data


# ═══════════════════════════════════════════════════════════════
# 4. PROMPT MASTER ESTRATEGIA SMC — IDENTICO AL ORIGINAL
# ═══════════════════════════════════════════════════════════════

def analyze_with_smc(all_data: list, btc_data: dict) -> dict:
    """
    Aplica la Master Estrategia SMC con los 5 pilares.
    Prompt completo e identico al bot original.
    Puede retornar NO_TRADE si no hay setup claro.
    """

    # Formatear datos de cada activo — igual que el original
    assets_text = ""
    for d in all_data:
        sym = d["symbol"]
        h4  = d.get("4h", {})
        h1  = d.get("1h", {})
        m15 = d.get("15m", {})
        if not h4 or not h1 or not m15:
            continue

        m30 = d.get("30m", {})
        assets_text += f"""
═══ {sym} ═══
TEMPORALIDAD 4H (TENDENCIA MACRO):
  Tendencia: {h4.get('trend')} | Precio: {h4.get('price')}
  EMA20: {h4.get('ema20')} | EMA50: {h4.get('ema50')} | EMA200: {h4.get('ema200')}
  Precio vs EMA20: {h4.get('price_vs_ema20')} | vs EMA50: {h4.get('price_vs_ema50')}
  RSI: {h4.get('rsi')} | MACD hist: {h4.get('macd_hist')} ({h4.get('macd_color')})
  Zona alta (resistencia): {h4.get('recent_high')} | Zona baja (soporte): {h4.get('recent_low')}
  Bollinger: sup={h4.get('bb_upper')} mid={h4.get('bb_mid')} inf={h4.get('bb_lower')} | Posicion: {h4.get('bb_pos')}%

TEMPORALIDAD 1H (DOBLE CONFIRMACION SMC - PRIMERA):
  Tendencia: {h1.get('trend')} | Precio: {h1.get('price')}
  EMA20: {h1.get('ema20')} | EMA50: {h1.get('ema50')}
  Precio vs EMA20: {h1.get('price_vs_ema20')} | vs EMA50: {h1.get('price_vs_ema50')}
  RSI: {h1.get('rsi')} | MACD hist: {h1.get('macd_hist')} ({h1.get('macd_color')})
  Zona alta: {h1.get('recent_high')} | Zona baja: {h1.get('recent_low')}
  Bollinger: sup={h1.get('bb_upper')} mid={h1.get('bb_mid')} inf={h1.get('bb_lower')} | Posicion: {h1.get('bb_pos')}%

TEMPORALIDAD 30M (DOBLE CONFIRMACION SMC - SEGUNDA):
  Tendencia: {m30.get('trend')} | Precio: {m30.get('price')}
  EMA20: {m30.get('ema20')} | EMA50: {m30.get('ema50')}
  Precio vs EMA20: {m30.get('price_vs_ema20')} | vs EMA50: {m30.get('price_vs_ema50')}
  RSI: {m30.get('rsi')} | MACD hist: {m30.get('macd_hist')} ({m30.get('macd_color')})
  Zona alta: {m30.get('recent_high')} | Zona baja: {m30.get('recent_low')}
  Bollinger: sup={m30.get('bb_upper')} mid={m30.get('bb_mid')} inf={m30.get('bb_lower')} | Posicion: {m30.get('bb_pos')}%

TEMPORALIDAD 15M (CONFIRMACION MACD - ENTRADA):
  Tendencia: {m15.get('trend')} | Precio: {m15.get('price')}
  MACD histograma ahora: {m15.get('macd_hist')} | anterior: {m15.get('macd_hist_prev')}
  COLOR MACD 15M: {m15.get('macd_color')} <-- DISPARO DE ENTRADA
  RSI: {m15.get('rsi')} | Bollinger posicion: {m15.get('bb_pos')}%
  Zona alta: {m15.get('recent_high')} | Zona baja: {m15.get('recent_low')}
  Volumen actual: {m15.get('vol_now')} | Promedio: {m15.get('vol_avg')}
"""

    btc_h4  = btc_data.get("4h", {})
    btc_h1  = btc_data.get("1h", {})
    btc_m15 = btc_data.get("15m", {})

    btc_text = f"""
BTC (ACOMPANAMIENTO):
  4H tendencia: {btc_h4.get('trend')} | MACD: {btc_h4.get('macd_color')}
  1H tendencia: {btc_h1.get('trend')} | MACD: {btc_h1.get('macd_color')}
  15M tendencia: {btc_m15.get('trend')} | MACD: {btc_m15.get('macd_color')} | RSI: {btc_m15.get('rsi')}
  Precio BTC: {btc_m15.get('price')}
"""

    prompt = f"""Eres un trader experto en Smart Money Concept (SMC) aplicando LA MASTER ESTRATEGIA con 5 pilares.

DATOS ACTUALES DEL MERCADO:
{assets_text}
{btc_text}

CONCEPTO CLAVE - MINI-TENDENCIAS DENTRO DE ZONAS DE REACCION:
El mercado opera en tendencias dentro de tendencias. ESTO ES FUNDAMENTAL:

ESCENARIO A - Tendencia macro ALCISTA + precio en RESISTENCIA:
  → El precio sube en 4H pero llega a una zona de resistencia fuerte
  → En 1H y 30M se forma una MINI-TENDENCIA BAJISTA dentro de esa resistencia
  → Esto es una oportunidad de VENTA valida aunque el 4H sea alcista
  → Señal: 4H alcista + precio en recent_high/bb_upper + 1H bajista o neutral + MACD 15M cambia verde→rojo
  → OPERAR: DOWN (venta desde resistencia)

ESCENARIO B - Tendencia macro BAJISTA + precio en SOPORTE:
  → El precio baja en 4H pero llega a una zona de soporte fuerte
  → En 1H y 30M se forma una MINI-TENDENCIA ALCISTA dentro de ese soporte
  → Esto es una oportunidad de COMPRA valida aunque el 4H sea bajista
  → Señal: 4H bajista + precio en recent_low/bb_lower + 1H alcista o neutral + MACD 15M cambia rojo→verde
  → OPERAR: UP (compra desde soporte)

ESCENARIO C - Tendencia clara en todas las temporalidades:
  → 4H, 1H y 30M todos alineados en la misma direccion
  → Precio pullback a EMA20 o zona de soporte/resistencia intermedia
  → MACD 15M confirma con cambio de color
  → OPERAR en direccion de la tendencia

APLICA LOS 5 PILARES DE LA MASTER ESTRATEGIA:

PILAR 1 - TENDENCIA MACRO Y MINI-TENDENCIA:
- Identifica la tendencia MACRO en 4H (direccion principal del mercado)
- Identifica la MINI-TENDENCIA en 1H y 30M (movimiento actual dentro de la macro)
- NO es necesario que macro y mini vayan en la misma direccion
- LO IMPORTANTE: la mini-tendencia debe estar confirmada en 1H y 30M
- Ejemplos validos:
  * 4H alcista + 1H bajista + precio en resistencia = VENTA valida
  * 4H bajista + 1H alcista + precio en soporte = COMPRA valida
  * 4H alcista + 1H alcista + precio en soporte = COMPRA valida (tendencia confirmada)

PILAR 2 - ZONA DE REACCION SMC:
El precio DEBE estar en una zona de alta probabilidad de reaccion:
- Zona de RESISTENCIA (para ventas):
  * Precio cerca de recent_high en 4H o 1H
  * bb_pos > 75% en 1H o 30M
  * RSI > 65 en 1H o 30M
  * Precio en maximo reciente con velas de rechazo
- Zona de SOPORTE (para compras):
  * Precio cerca de recent_low en 4H o 1H
  * bb_pos < 25% en 1H o 30M
  * RSI < 35 en 1H o 30M
  * Precio en minimo reciente con velas de rebote
- Si el precio esta en zona media sin referencia clara -> NO operar

PILAR 3 - POSICIONAMIENTO (precio dentro de la zona):
- Para VENTA: precio debe estar tocando o dentro de la zona de resistencia
  * bb_pos > 70% en al menos 2 temporalidades
  * O precio muy cerca del recent_high de 1H o 4H
- Para COMPRA: precio debe estar tocando o dentro de la zona de soporte
  * bb_pos < 30% en al menos 2 temporalidades
  * O precio muy cerca del recent_low de 1H o 4H
- Si el precio ignora la zona y rompe con fuerza -> NO operar (no perseguir)

PILAR 4 - CONFIRMACION MACD 15M (EL DISPARO DE ENTRADA - MAS IMPORTANTE):
Este es el gatillo final. Sin esto NO se entra aunque todo lo demas este perfecto.
- Para VENTA: MACD 15M debe mostrar debilitamiento alcista:
  * macd_color = "cambio_verde_a_rojo" (cruce bajista) <- SEÑAL PERFECTA
  * macd_color = "verde_claro" (histograma verde reduciendose) <- SEÑAL BUENA
- Para COMPRA: MACD 15M debe mostrar debilitamiento bajista:
  * macd_color = "cambio_rojo_a_verde" (cruce alcista) <- SEÑAL PERFECTA
  * macd_color = "rojo_claro" (histograma rojo reduciendose) <- SEÑAL BUENA
- Si MACD 15M es verde_fuerte y queremos vender -> ESPERAR
- Si MACD 15M es rojo_fuerte y queremos comprar -> ESPERAR

PILAR 5 - ACOMPANAMIENTO BTC:
BTC debe estar alineado con la operacion que vamos a hacer:
- Para VENTA en altcoin: BTC debe mostrar debilidad (bajista en 1H o MACD 15M debilitandose)
- Para COMPRA en altcoin: BTC debe mostrar fuerza (alcista en 1H o MACD 15M fortaleciendo)
- BTC no necesita cumplir todos los pilares, solo estar alineado en direccion
- Si BTC va claramente en contra -> reducir confianza o NO operar

REGLAS CRITICAS DE LA MASTER ESTRATEGIA:
1. Solo operar cuando el precio ESTA en la zona, no cuando se acerca
2. Si el precio llega a la zona pero la ignora y sigue con fuerza -> NO operar
3. Esperar siempre la confirmacion del MACD 15M antes de entrar
4. Es mejor perderse una operacion que entrar sin confirmacion
5. Minimo 4 de 5 pilares deben confirmarse para operar
6. La mini-tendencia en 1H/30M es MAS importante que la tendencia macro en 4H

RESPONDE UNICAMENTE con este JSON exacto:
{{
  "action": "TRADE" o "NO_TRADE",
  "symbol": "SOLUSDT" o "XRPUSDT" o "ETHUSDT" (solo si action=TRADE),
  "signal": "UP" o "DOWN" (solo si action=TRADE),
  "confidence": numero 0.0-1.0,
  "pilares_cumplidos": numero 1-5 de pilares que se cumplen,
  "tendencia_macro": "descripcion de la tendencia en 4H",
  "mini_tendencia": "descripcion de la mini-tendencia en 1H/30M",
  "zona_reaccion": "descripcion de la zona donde esta el precio",
  "macd_confirmacion": "descripcion del estado del MACD 15M",
  "btc_alineado": true o false,
  "razon": "explicacion clara de por que operar o no operar mencionando tendencia macro mini-tendencia zona y MACD"
}}"""

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        # Reparar JSON truncado
        if raw.count("{") > raw.count("}"):
            raw = raw + "}" * (raw.count("{") - raw.count("}"))
        result = json.loads(raw)
        return result
    except Exception as e:
        log.error(f"Error IA SMC: {e}")
        return {"action": "NO_TRADE", "confidence": 0, "razon": f"Error IA: {e}"}


# ═══════════════════════════════════════════════════════════════
# 5. OPERACIONES POR USUARIO
# ═══════════════════════════════════════════════════════════════

def calc_tp_sl(order_usdt: float, leverage: int) -> tuple:
    """
    Regla de tres proporcional al capital del usuario.
    Base de referencia: $1000 x 10x → TP +$37 | SL -$60
    Retorna: (tp_usd, sl_usd, commission_usd)
    """
    controlled = order_usdt * leverage
    base_controlled = BASE_CAPITAL * BASE_LEVERAGE
    ratio    = controlled / base_controlled
    tp_usd   = round(BASE_TP_USD * ratio, 2)
    sl_usd   = round(BASE_SL_USD * ratio, 2)
    comm_usd = round(controlled * COMMISSION_PCT, 4)
    return tp_usd, sl_usd, comm_usd

def get_qty_user(client: HTTP, symbol: str, order_usdt: float, leverage: int) -> str:
    try:
        price    = get_price(client, symbol)
        info     = client.get_instruments_info(category="linear", symbol=symbol)
        lot      = info["result"]["list"][0]["lotSizeFilter"]
        qty_step = float(lot.get("qtyStep") or lot.get("basePrecision") or 0.001)
        min_qty  = float(lot.get("minOrderQty") or 0.001)
        qty      = max(min_qty, round((order_usdt * leverage / price) / qty_step) * qty_step)
        controlled = round(qty * price, 2)
        commission = round(controlled * COMMISSION_PCT, 4)
        log.info(f"    Posicion: {qty} {symbol} | ${controlled:,} controlados | comision aprox: ${commission}")
        return str(round(qty, 8))
    except Exception as e:
        log.error(f"    Error qty: {e}")
        return "0"

def open_order_user(client: HTTP, symbol: str, side: str, qty: str, leverage: int) -> str | None:
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
        oid = resp["result"].get("orderId","N/A")
        log.info(f"    ORDEN {side} {qty} {symbol} | ID: {oid}")
        return oid
    except Exception as e:
        log.error(f"    Error orden: {e}")
        return None

def close_order_user(client: HTTP, symbol: str) -> float:
    try:
        positions = client.get_positions(category="linear", symbol=symbol)
        pos_list  = positions["result"]["list"]
        if not pos_list or float(pos_list[0]["size"]) == 0:
            return get_price(client, symbol)
        pos  = pos_list[0]
        side = "Sell" if pos["side"] == "Buy" else "Buy"
        client.place_order(
            category="linear", symbol=symbol,
            side=side, orderType="Market",
            qty=pos["size"], reduceOnly=True, timeInForce="IOC"
        )
        log.info(f"    Posicion cerrada {symbol}")
        return get_price(client, symbol)
    except Exception as e:
        log.error(f"    Error cerrando: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════════════
# 6. LOG DE TRADES
# ═══════════════════════════════════════════════════════════════

def save_trade(user_id, symbol, signal, confidence, entry, close_px,
               order_usdt, leverage, pilares, razon):
    controlled = order_usdt * leverage
    commission = round(controlled * COMMISSION_PCT, 4)
    pnl_pct    = round((close_px-entry)/entry*100, 4) if signal=="UP" else round((entry-close_px)/entry*100, 4)
    pnl_usd    = round(controlled * pnl_pct / 100 - commission, 4)
    result     = "WIN" if pnl_usd > 0 else "LOSS"

    # Guardar en CSV (thread-safe)
    with csv_lock:
        exists = os.path.isfile(LOG_FILE)
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["timestamp","user_id","symbol","signal","confidence","pilares",
                            "order_usdt","leverage","entry","close","pnl_pct","pnl_usd",
                            "commission","result","razon"])
            w.writerow([
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                user_id[:8]+"...",   # parcial por privacidad
                symbol, signal, confidence, pilares,
                order_usdt, leverage, entry, close_px,
                pnl_pct, pnl_usd, commission, result, razon
            ])

    # Guardar en Supabase
    try:
        sb.table("trades_log").insert({
            "user_id":     user_id,
            "symbol":      symbol,
            "signal":      signal,
            "confidence":  confidence,
            "pilares":     pilares,
            "order_usdt":  order_usdt,
            "leverage":    leverage,
            "entry":       entry,
            "close_price": close_px,
            "pnl_pct":     pnl_pct,
            "pnl_usd":     pnl_usd,
            "commission":  commission,
            "result":      result,
            "razon":       razon
        }).execute()
    except Exception as e:
        log.warning(f"    Error guardando trade en Supabase: {e}")

    return result, pnl_usd


def write_state(scan_count: int, active_positions: dict, last_decision: dict = None):
    """Escribe estado global del bot para monitoreo externo."""
    state = {
        "status":           "running",
        "check_every":      CHECK_EVERY,
        "scan_count":       scan_count,
        "active_positions": active_positions,
        "last_decision":    last_decision or {},
        "last_update":      datetime.now(timezone.utc).isoformat()
    }
    try:
        with open("bot_state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def print_summary():
    """Imprime resumen de operaciones al finalizar."""
    if not os.path.isfile(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    if df.empty:
        return
    r = df[df["result"].isin(["WIN","LOSS"])]
    if r.empty:
        return
    total   = len(r)
    wins    = len(r[r["result"]=="WIN"])
    acc     = round(wins/total*100, 1)
    pnl_usd = round(r["pnl_usd"].sum(), 2)
    avg_op  = round(r["pnl_usd"].mean(), 2)
    print(f"\n{'='*55}")
    print(f"  {total} operaciones | {acc}% precision | Total: ${pnl_usd:+.2f} | Promedio: ${avg_op:+.2f}/op")
    print(f"{'='*55}\n")


# ═══════════════════════════════════════════════════════════════
# 7. HILO POR USUARIO — opera y monitorea su posicion
# ═══════════════════════════════════════════════════════════════

def user_trade_thread(user: dict, decision: dict, active_positions: dict):
    """
    Corre en un hilo separado por cada usuario.
    Abre la orden, monitorea con la misma logica TP/SL del bot original,
    y cierra cuando se alcanza TP, SL o timeout de 4 horas.
    """
    uid        = user["user_id"]
    api_key    = user["bybit_api_key"]
    api_secret = user["bybit_api_secret"]
    order_usdt = float(user.get("order_usdt") or 100)   # configurado desde interfaz grafica
    leverage   = int(user.get("leverage") or 10)         # configurado desde interfaz grafica

    symbol     = decision["symbol"]
    signal     = decision["signal"]
    confidence = decision["confidence"]
    pilares    = decision["pilares_cumplidos"]
    razon      = decision["razon"]
    side       = "Buy" if signal == "UP" else "Sell"

    # TP y SL proporcionales al capital del usuario (regla de tres)
    tp_usd, sl_usd, comm = calc_tp_sl(order_usdt, leverage)
    controlled = order_usdt * leverage

    log.info(f"  [Usuario {uid[:8]}] Capital: ${order_usdt} x{leverage} = ${controlled} "
             f"| TP: +${tp_usd} | SL: -${sl_usd} | Comision: ~${comm}")

    try:
        client = HTTP(testnet=False, demo=True, api_key=api_key, api_secret=api_secret)
    except Exception as e:
        log.error(f"  [Usuario {uid[:8]}] Error creando cliente Bybit: {e}")
        return

    qty = get_qty_user(client, symbol, order_usdt, leverage)
    if qty == "0":
        log.error(f"  [Usuario {uid[:8]}] Cantidad invalida, omitiendo")
        return

    order_id = open_order_user(client, symbol, side, qty, leverage)
    if not order_id:
        log.error(f"  [Usuario {uid[:8]}] Orden fallida")
        return

    entry_px  = get_price(client, symbol)
    open_time = datetime.now(timezone.utc)

    log.info(f"  [Usuario {uid[:8]}] ✅ OPERACION ABIERTA: {signal} {symbol} @ ${entry_px}")
    log.info(f"  [Usuario {uid[:8]}]    TP: ~${round(entry_px * (1 + tp_usd/controlled), 4)} "
             f"| SL: ~${round(entry_px * (1 - sl_usd/controlled), 4)}")

    # Registrar posicion activa (para write_state)
    active_positions[uid] = {
        "symbol":    symbol,
        "signal":    signal,
        "entry":     entry_px,
        "open_time": open_time.isoformat()
    }

    # ── Monitorear igual que el bot original ──
    while True:
        time.sleep(CHECK_EVERY)
        current_px = get_price(client, symbol)
        if current_px == 0:
            continue

        if signal == "UP":
            pnl_usd = round(controlled * (current_px - entry_px) / entry_px - comm, 2)
        else:
            pnl_usd = round(controlled * (entry_px - current_px) / entry_px - comm, 2)

        elapsed = (datetime.now(timezone.utc) - open_time).seconds / 60

        log.info(f"  [Usuario {uid[:8]}] {symbol} {signal} | "
                 f"Entrada: {entry_px} | Actual: {current_px} | "
                 f"PnL: ${pnl_usd:+.2f} | Tiempo: {elapsed:.0f}m")

        should_close = False
        close_reason = ""

        if pnl_usd >= tp_usd:
            should_close = True
            close_reason = f"Take Profit alcanzado: ${pnl_usd:+.2f}"
        elif pnl_usd <= -sl_usd:
            should_close = True
            close_reason = f"Stop Loss alcanzado: ${pnl_usd:+.2f}"
        elif elapsed >= 240:
            should_close = True
            close_reason = f"Tiempo maximo (4h) alcanzado: ${pnl_usd:+.2f}"

        if should_close:
            close_px = close_order_user(client, symbol)
            result, final_pnl = save_trade(
                uid, symbol, signal, confidence, entry_px, close_px,
                order_usdt, leverage, pilares, razon
            )
            log.info(f"  [Usuario {uid[:8]}] {'✅ WIN' if result=='WIN' else '❌ LOSS'} "
                     f"| {close_reason} | PnL final: ${final_pnl:+.2f}")
            # Eliminar de posiciones activas
            active_positions.pop(uid, None)
            break


# ═══════════════════════════════════════════════════════════════
# 8. BUCLE PRINCIPAL — Monitoreo continuo con paciencia SMC
# ═══════════════════════════════════════════════════════════════

def run():
    log.info("=" * 65)
    log.info("  MASTER ESTRATEGIA BOT — MULTI-USUARIO INICIADO")
    log.info("=" * 65)
    log.info(f"Activos: {SYMBOLS} | BTC acompanamiento: {BTC_SYMBOL}")
    log.info("Estrategia: SMC + MACD 15M + Tendencia 4H/1H + Acompanamiento BTC")
    log.info("Capital y leverage: configurados por usuario desde interfaz grafica")
    log.info("Filosofia: Solo opera cuando TODOS los pilares alinean. Paciencia es clave.\n")

    scan_count       = 0
    last_decision    = {}
    active_positions = {}   # {user_id: {...}} — para write_state

    try:
        while True:
            scan_count += 1
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

            log.info(f"\n[{ts}] SCAN #{scan_count} | Analizando mercado con Master Estrategia SMC...")

            # 1. Cargar usuarios activos desde Supabase
            users = get_active_users()
            if not users:
                log.warning("  Sin usuarios activos. Esperando...")
                write_state(scan_count, active_positions, last_decision)
                time.sleep(CHECK_EVERY)
                continue

            log.info(f"  {len(users)} usuario(s) activo(s)")

            # 2. Analisis de mercado compartido (una sola llamada para todos los usuarios)
            btc_data = get_full_analysis(BTC_SYMBOL)
            all_data = []
            for sym in SYMBOLS:
                data = get_full_analysis(sym)
                all_data.append(data)
                time.sleep(0.3)

            # 3. IA aplica los 5 pilares SMC
            decision      = analyze_with_smc(all_data, btc_data)
            last_decision = decision
            write_state(scan_count, active_positions, last_decision)

            action     = decision.get("action", "NO_TRADE")
            pilares    = decision.get("pilares_cumplidos", 0)
            confidence = decision.get("confidence", 0)
            razon      = decision.get("razon", "")
            macd_conf  = decision.get("macd_confirmacion", "")
            btc_ok     = decision.get("btc_alineado", False)
            zona       = decision.get("zona_reaccion", "")

            tendencia_macro = decision.get("tendencia_macro", "")
            mini_tendencia  = decision.get("mini_tendencia", "")

            log.info(f"  Decision IA: {action} | Pilares: {pilares}/5 | Confianza: {confidence}")
            log.info(f"  Tendencia macro: {tendencia_macro}")
            log.info(f"  Mini-tendencia:  {mini_tendencia}")
            log.info(f"  Zona:            {zona}")
            log.info(f"  MACD 15M:        {macd_conf}")
            log.info(f"  BTC alineado:    {btc_ok}")
            log.info(f"  Razon:           {razon}")

            # 4. Si hay señal valida, lanzar hilo por cada usuario
            if action == "TRADE" and pilares >= 4:
                symbol = decision.get("symbol", SYMBOLS[0])
                signal = decision.get("signal", "UP")
                log.info(f"\n  🚀 SEÑAL DETECTADA: {signal} {symbol}")
                log.info(f"  Abriendo ordenes para {len(users)} usuario(s)...\n")

                threads = []
                for user in users:
                    t = threading.Thread(
                        target=user_trade_thread,
                        args=(user, decision, active_positions),
                        daemon=True,
                        name=f"user-{user['user_id'][:8]}"
                    )
                    t.start()
                    threads.append(t)
                    time.sleep(0.5)  # pequeño delay entre usuarios

                log.info(f"  {len(threads)} hilo(s) de trading iniciados")

                # Esperar a que todos cierren sus posiciones antes del siguiente scan
                for t in threads:
                    t.join()

                log.info("  Todos los usuarios cerraron sus posiciones en este ciclo.")

            else:
                if action == "NO_TRADE":
                    log.info("  SIN SETUP: Esperando mejor oportunidad...")
                else:
                    log.info(f"  Pilares insuficientes ({pilares}/5): No se opera. Se requieren minimo 4.")

            log.info(f"  Proximo scan en {CHECK_EVERY}s...")
            time.sleep(CHECK_EVERY)

    except KeyboardInterrupt:
        log.info("\nBot detenido por usuario")
        log.info("Nota: los hilos con posiciones abiertas son daemon=True y se detendran.")
        print_summary()


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    missing = []
    if not SUPABASE_URL:         missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_KEY: missing.append("SUPABASE_SERVICE_KEY")
    if not ANTHROPIC_API_KEY:    missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"ERROR: Faltan variables de entorno: {', '.join(missing)}")
        exit(1)
    run()