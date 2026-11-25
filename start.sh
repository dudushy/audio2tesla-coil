#!/usr/bin/env bash
# Sobe o app e mostra o endereço para abrir em outros aparelhos da mesma rede.
#
#   ./start.sh                      docker compose up -d --build + endereços
#   ./start.sh --ip                 só mostra os endereços
#   ./start.sh --liberar-firewall   WSL "mirrored": libera a porta no Windows (pede admin)
set -euo pipefail
cd "$(dirname "$0")"

PORT=1337
# firewall Hyper-V: ID fixo da VM do WSL e nome da regra criada por este script
WSL_VM='{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
RULE="audio2tesla-coil-$PORT"

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }
wsl_mirrored() { is_wsl && [ "$(wslinfo --networking-mode 2>/dev/null)" = mirrored ]; }
ps_run() { powershell.exe -NoProfile -NonInteractive -Command "$1" 2>/dev/null | tr -d '\r'; }

# IPv4 da placa que tem gateway (ignora redes virtuais do Docker/Hyper-V)
lan_ip() {
  if is_wsl && command -v powershell.exe >/dev/null; then
    # no WSL em modo NAT o `ip` mostra o IP interno da VM; o que vale é o do Windows
    ps_run '(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
      Sort-Object { $_.IPv4DefaultGateway.RouteMetric + $_.NetIPv4Interface.InterfaceMetric } |
      Select-Object -First 1).IPv4Address.IPAddress'
  elif command -v ip >/dev/null; then
    ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p'
  else # macOS
    ipconfig getifaddr "$(route -n get default | awk '/interface:/ {print $2}')"
  fi
}

firewall_ok() {
  [ -n "$(ps_run "Get-NetFirewallHyperVRule -Name '$RULE' -ErrorAction SilentlyContinue")" ]
}

allow_firewall() {
  if ! wsl_mirrored; then
    echo "Só é preciso no WSL em modo de rede 'mirrored'; nada a fazer aqui."
    return
  fi
  local cmd="New-NetFirewallHyperVRule -Name '$RULE' -DisplayName 'audio2tesla-coil ($PORT)' -Direction Inbound -VMCreatorId '$WSL_VM' -Protocol TCP -LocalPorts $PORT -Action Allow"
  echo "Aceite o aviso de administrador (UAC) que vai abrir no Windows..."
  # -EncodedCommand evita problemas de aspas ao passar o comando para o processo elevado
  local enc
  enc=$(printf '%s' "$cmd" | iconv -f utf-8 -t utf-16le | base64 -w0)
  ps_run "Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList '-NoProfile','-EncodedCommand','$enc'" || true
  if firewall_ok; then
    echo "Porta $PORT liberada."
  else
    echo "A regra não foi criada. Rode num PowerShell como administrador:"
    echo "  $cmd"
    return 1
  fi
}

show_urls() {
  local ip
  ip=$(lan_ip || true)
  echo
  echo "  Neste computador:     http://localhost:$PORT"
  if docker compose port coil "$PORT" 2>/dev/null | grep -q '^127\.'; then
    echo "  Rede local:           desativada (COIL_BIND=127.0.0.1)"
  elif [ -n "$ip" ]; then
    echo "  Outros aparelhos:     http://$ip:$PORT"
    if wsl_mirrored && ! firewall_ok; then
      echo
      echo "  AVISO: o firewall do WSL ainda bloqueia a porta $PORT para a rede."
      echo "  Rode uma vez: ./start.sh --liberar-firewall"
    fi
  else
    echo "  Outros aparelhos:     IP da rede não encontrado (o computador está conectado?)"
  fi
  echo
}

case "${1:-}" in
  --ip) show_urls ;;
  --liberar-firewall) allow_firewall && show_urls ;;
  "")
    docker compose up -d --build
    show_urls
    echo "  Logs: docker compose logs -f    Parar: docker compose down"
    ;;
  *)
    echo "uso: ./start.sh [--ip | --liberar-firewall]" >&2
    exit 2
    ;;
esac
