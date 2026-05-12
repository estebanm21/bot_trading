"""
Analiza el archivo trades_log.csv generado por el bot
y muestra estadísticas detalladas de rendimiento.

Uso: python analyze.py
"""

import os
import pandas as pd

LOG_FILE = "trades_log.csv"

def analyze():
    if not os.path.isfile(LOG_FILE):
        print("❌ No se encontró trades_log.csv. Ejecuta el bot primero.")
        return

    df = pd.read_csv(LOG_FILE)
    if df.empty:
        print("❌ El archivo de log está vacío.")
        return

    # Filtrar solo operaciones resueltas
    df = df[df["result"].isin(["WIN", "LOSS"])]

    print("\n" + "═"*60)
    print("           📊 ANÁLISIS DE RENDIMIENTO DEL BOT")
    print("═"*60)

    # ── Global ──
    total  = len(df)
    wins   = len(df[df["result"] == "WIN"])
    losses = len(df[df["result"] == "LOSS"])
    acc    = round(wins / total * 100, 1) if total > 0 else 0
    avg_pnl = round(df["pnl_pct"].mean(), 3)
    total_pnl = round(df["pnl_pct"].sum(), 2)

    print(f"\n  GLOBAL:")
    print(f"    Operaciones totales : {total}")
    print(f"    Wins / Losses       : {wins} ✅ / {losses} ❌")
    print(f"    Precisión           : {acc}%")
    print(f"    PnL promedio/op     : {avg_pnl}%")
    print(f"    PnL total acumulado : {total_pnl}%")

    # ── Por símbolo ──
    print(f"\n  POR ACTIVO:")
    for sym, grp in df.groupby("symbol"):
        sym_total = len(grp)
        sym_wins  = len(grp[grp["result"] == "WIN"])
        sym_acc   = round(sym_wins / sym_total * 100, 1)
        sym_pnl   = round(grp["pnl_pct"].sum(), 2)
        print(f"    {sym:<12} Ops: {sym_total:3d} | Acc: {sym_acc:5.1f}% | PnL: {sym_pnl:+.2f}%")

    # ── Por señal ──
    print(f"\n  POR SEÑAL:")
    for sig, grp in df.groupby("signal"):
        sig_total = len(grp)
        sig_wins  = len(grp[grp["result"] == "WIN"])
        sig_acc   = round(sig_wins / sig_total * 100, 1)
        print(f"    {sig:<6}  Ops: {sig_total:3d} | Acc: {sig_acc:5.1f}%")

    # ── Por confianza ──
    print(f"\n  POR NIVEL DE CONFIANZA:")
    bins = [(0.65, 0.75), (0.75, 0.85), (0.85, 1.01)]
    for lo, hi in bins:
        grp = df[(df["confidence"] >= lo) & (df["confidence"] < hi)]
        if len(grp) == 0:
            continue
        wins_g = len(grp[grp["result"] == "WIN"])
        acc_g  = round(wins_g / len(grp) * 100, 1)
        print(f"    {lo:.2f}-{hi:.2f}  Ops: {len(grp):3d} | Acc: {acc_g:5.1f}%")

    # ── Últimas 10 ops ──
    print(f"\n  ÚLTIMAS 10 OPERACIONES:")
    last10 = df.tail(10)[["timestamp", "symbol", "signal", "confidence", "pnl_pct", "result"]]
    for _, row in last10.iterrows():
        icon = "✅" if row["result"] == "WIN" else "❌"
        print(f"    {icon} {row['timestamp'][:16]} | {row['symbol']:<12} | {row['signal']} | conf:{row['confidence']} | {row['pnl_pct']:+.2f}%")

    print("\n" + "═"*60 + "\n")

if __name__ == "__main__":
    analyze()
