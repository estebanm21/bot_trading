# 🤖 Crypto Bot — Bybit Testnet + Claude AI

Bot de trading automatizado que predice UP/DOWN cada 5 minutos usando
indicadores técnicos analizados por Claude (IA de Anthropic).

---

## 📁 Archivos

```
nodion_bot/
├── bot.py          ← Bot principal
├── analyze.py      ← Análisis de resultados
├── .env.example    ← Plantilla de configuración
└── README.md       ← Este archivo
```

---

## ⚡ Instalación rápida

### 1. Instalar dependencias

```bash
pip install pybit pandas pandas-ta anthropic python-dotenv requests
```

### 2. Crear cuenta en Bybit Testnet

1. Entra a https://testnet.bybit.com
2. Crea una cuenta gratuita
3. Ve a **Mi perfil → API Management → Crear API Key**
4. Permisos necesarios: ✅ Read, ✅ Trade (Derivatives/USDT Perpetual)
5. Guarda tu `API Key` y `API Secret`

### 3. Obtener API Key de Claude (Anthropic)

1. Entra a https://console.anthropic.com
2. Ve a **API Keys → Create Key**
3. Guarda tu clave

### 4. Configurar credenciales

```bash
# Copia el ejemplo
cp .env.example .env

# Edita con tus datos reales
nano .env   # o abre con cualquier editor de texto
```

Rellena tu `.env`:
```
BYBIT_API_KEY=tu_api_key_real
BYBIT_API_SECRET=tu_api_secret_real
ANTHROPIC_API_KEY=tu_anthropic_key_real
```

---

## 🚀 Uso

### Correr el bot
```bash
python bot.py
```

El bot:
1. Espera al inicio de la próxima ventana de 5 minutos
2. Analiza BTC, ETH, SOL y XRP con 50 velas históricas
3. Calcula RSI, MACD, EMA, Bollinger Bands, ATR
4. Envía los indicadores a Claude para que decida UP/DOWN
5. Si la confianza >= 0.65, abre una orden de mercado
6. Al inicio de la siguiente ventana, cierra la posición y registra el resultado
7. Repite indefinidamente

### Detener el bot
```
Ctrl + C
```
Al detener, muestra un resumen de la sesión.

### Ver estadísticas
```bash
python analyze.py
```

---

## ⚙️ Configuración avanzada (en bot.py)

| Parámetro | Valor por defecto | Descripción |
|-----------|------------------|-------------|
| `SYMBOLS` | BTC, ETH, SOL, XRP | Activos a operar |
| `ORDER_USDT` | 10 | Capital por operación (USDT) |
| `WINDOW_MINUTES` | 5 | Duración de cada ventana |
| `LOOKBACK_CANDLES` | 50 | Velas históricas para análisis |
| `confidence >= 0.65` | 0.65 | Umbral mínimo para entrar |

---

## 📊 Archivos generados

- `trades_log.csv` → Historial completo de operaciones
- `bot.log` → Log detallado del bot

### Columnas del CSV:
| Columna | Descripción |
|---------|-------------|
| window | Número de ventana |
| timestamp | Fecha y hora UTC |
| symbol | Activo operado |
| signal | UP o DOWN |
| confidence | Confianza de la IA (0.65-1.0) |
| entry_price | Precio de entrada |
| close_price | Precio al cierre de ventana |
| pnl_pct | PnL en % |
| result | WIN o LOSS |
| reason | Justificación de la IA |

---

## ⚠️ Notas importantes

- **Este bot usa Bybit TESTNET** — dinero ficticio, sin riesgo real
- Una precisión >55% sostenida es considerada buena en mercados crypto
- El umbral de confianza 0.65 filtra señales débiles
- Prueba durante al menos 100 operaciones antes de evaluar
- **No uses dinero real sin probar exhaustivamente primero**

---

## 🔁 Flujo de una ventana

```
[Inicio ventana]
      ↓
Cierra posiciones anteriores → registra WIN/LOSS
      ↓
Obtiene 50 velas de cada activo
      ↓
Calcula indicadores técnicos
      ↓
Claude analiza → UP/DOWN + confianza
      ↓
Si confianza >= 0.65 → abre orden
      ↓
[Espera 5 minutos → siguiente ventana]
```
