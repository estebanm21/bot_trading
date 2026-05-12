#!/bin/bash
# ═══════════════════════════════════════════════════
#  SETUP AUTOMATICO — ORACLE CLOUD VM
#  Ejecutar una sola vez despues de conectarte por SSH
# ═══════════════════════════════════════════════════
set -e

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   BYBIT SMC BOT — SETUP SERVIDOR    ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 1. Actualizar sistema
echo "[1/6] Actualizando sistema..."
sudo apt update -qq && sudo apt upgrade -y -qq

# 2. Instalar Python 3 y herramientas
echo "[2/6] Instalando Python y dependencias del sistema..."
sudo apt install -y -qq python3 python3-pip screen curl

# 3. Instalar dependencias del bot
echo "[3/6] Instalando librerias del bot..."
pip3 install --quiet pybit anthropic pandas ta python-dotenv flask

# 4. Abrir puerto 5001 en el firewall de Ubuntu
echo "[4/6] Configurando firewall (UFW)..."
sudo ufw allow 22/tcp   > /dev/null 2>&1
sudo ufw allow 5001/tcp > /dev/null 2>&1
sudo ufw --force enable > /dev/null 2>&1
echo "      Puertos abiertos: 22 (SSH), 5001 (Dashboard)"

# 5. Crear estructura de carpetas
echo "[5/6] Creando estructura de carpetas..."
mkdir -p ~/bot/templates

# 6. Crear servicio systemd para el bot (auto-reinicio si crashea o VM se reinicia)
echo "[6/6] Configurando servicios systemd..."

sudo tee /etc/systemd/system/smc-bot.service > /dev/null <<EOF
[Unit]
Description=Bybit SMC Trading Bot
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=15
StandardOutput=append:/home/$USER/bot/bot.log
StandardError=append:/home/$USER/bot/bot.log

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/smc-dashboard.service > /dev/null <<EOF
[Unit]
Description=Bybit SMC Dashboard
After=network.target smc-bot.service

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/bot
ExecStart=/usr/bin/python3 dashboard.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable smc-bot.service
sudo systemctl enable smc-dashboard.service

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  SETUP COMPLETO                                      ║"
echo "║                                                      ║"
echo "║  Siguiente paso: sube tus archivos y crea el .env   ║"
echo "║  Luego corre:  bash start_bot.sh                    ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
