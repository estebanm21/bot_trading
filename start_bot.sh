#!/bin/bash
# ═══════════════════════════════════════════════
#  INICIAR / REINICIAR EL BOT Y DASHBOARD
# ═══════════════════════════════════════════════

echo ""
echo "Iniciando servicios..."

sudo systemctl restart smc-bot.service
sudo systemctl restart smc-dashboard.service

sleep 2

echo ""
echo "Estado actual:"
echo "──────────────────────────────────────"
sudo systemctl status smc-bot.service       --no-pager -l | grep -E "Active|Error|Loaded"
sudo systemctl status smc-dashboard.service --no-pager -l | grep -E "Active|Error|Loaded"
echo "──────────────────────────────────────"
echo ""
echo "Ver logs del bot en tiempo real:"
echo "  tail -f ~/bot/bot.log"
echo ""
echo "Detener el bot:"
echo "  sudo systemctl stop smc-bot.service"
echo ""
IP=$(curl -s ifconfig.me 2>/dev/null || echo "tu-ip")
echo "Dashboard disponible en: http://$IP:5001"
echo ""
