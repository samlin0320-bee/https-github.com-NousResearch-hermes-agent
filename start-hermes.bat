@echo off
chcp 65001 > nul
echo Starting Hermes Agent in WSL2...
wsl bash -c "export LANG=C.UTF-8 && export LC_ALL=C.UTF-8 && cd /root/.hermes/hermes-agent && source venv/bin/activate && python hermes %*"
