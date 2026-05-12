"""
Dashboard Flask — BYBIT SMC BOT
Correr con: python dashboard.py
Luego abrir: http://localhost:5001
"""

from flask import Flask, render_template, jsonify
import json, os
import pandas as pd

app = Flask(__name__)

STATE_FILE  = "bot_state.json"
LOG_FILE    = "trades_log.csv"
BOT_LOG     = "bot.log"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({
            "status": "offline",
            "scan_count": 0,
            "active_position": None,
            "last_decision": {},
            "last_update": None,
            "check_every": 120
        })


@app.route("/api/stats")
def api_stats():
    try:
        df = pd.read_csv(LOG_FILE)
        r  = df[df["result"].isin(["WIN", "LOSS"])]
        total = len(r)
        if total == 0:
            raise ValueError("empty")
        wins   = len(r[r["result"] == "WIN"])
        losses = total - wins
        return jsonify({
            "total":     total,
            "wins":      wins,
            "losses":    losses,
            "win_rate":  round(wins / total * 100, 1),
            "total_pnl": round(float(r["pnl_usd"].sum()), 2),
            "avg_pnl":   round(float(r["pnl_usd"].mean()), 2),
        })
    except Exception:
        return jsonify({"total": 0, "wins": 0, "losses": 0,
                        "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0})


@app.route("/api/trades")
def api_trades():
    try:
        df = pd.read_csv(LOG_FILE)
        return jsonify(df.iloc[::-1].head(50).to_dict(orient="records"))
    except Exception:
        return jsonify([])


@app.route("/api/analysis")
def api_analysis():
    empty = {"by_symbol": [], "by_signal": [], "by_confidence": [], "last10": []}
    try:
        df = pd.read_csv(LOG_FILE)
        r  = df[df["result"].isin(["WIN", "LOSS"])]
        if r.empty:
            return jsonify(empty)

        # Por activo
        by_symbol = []
        for sym, grp in r.groupby("symbol"):
            t = len(grp); w = len(grp[grp["result"] == "WIN"])
            by_symbol.append({
                "symbol":   sym,
                "total":    t,
                "wins":     w,
                "losses":   t - w,
                "win_rate": round(w / t * 100, 1),
                "pnl_usd":  round(float(grp["pnl_usd"].sum()), 2),
                "avg_pnl":  round(float(grp["pnl_usd"].mean()), 2),
            })

        # Por señal
        by_signal = []
        for sig, grp in r.groupby("signal"):
            t = len(grp); w = len(grp[grp["result"] == "WIN"])
            by_signal.append({
                "signal":   sig,
                "total":    t,
                "wins":     w,
                "losses":   t - w,
                "win_rate": round(w / t * 100, 1),
                "pnl_usd":  round(float(grp["pnl_usd"].sum()), 2),
            })

        # Por confianza
        by_confidence = []
        for lo, hi, label in [(0.0, 0.65, "< 0.65"), (0.65, 0.75, "0.65–0.75"),
                               (0.75, 0.85, "0.75–0.85"), (0.85, 1.01, "> 0.85")]:
            grp = r[(r["confidence"] >= lo) & (r["confidence"] < hi)]
            if grp.empty:
                continue
            t = len(grp); w = len(grp[grp["result"] == "WIN"])
            by_confidence.append({
                "range":    label,
                "total":    t,
                "wins":     w,
                "win_rate": round(w / t * 100, 1),
                "pnl_usd":  round(float(grp["pnl_usd"].sum()), 2),
            })

        # Últimas 10
        last10 = r.tail(10).iloc[::-1][
            ["timestamp", "symbol", "signal", "confidence", "pilares", "pnl_pct", "pnl_usd", "result"]
        ].to_dict(orient="records")

        return jsonify({"by_symbol": by_symbol, "by_signal": by_signal,
                        "by_confidence": by_confidence, "last10": last10})
    except Exception:
        return jsonify(empty)


@app.route("/api/logs")
def api_logs():
    try:
        with open(BOT_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        return jsonify([l.rstrip() for l in lines[-80:]])
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    print("=" * 50)
    print("  BYBIT SMC BOT — DASHBOARD")
    print("  Abre en tu navegador: http://localhost:5001")
    print("=" * 50)
    app.run(port=5001, debug=False)
