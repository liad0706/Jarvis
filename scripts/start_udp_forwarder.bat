@echo off
title UDP Forwarder for RuView
echo Starting UDP Forwarder (port 5006 -> Docker 172.17.0.5:5005)...
python "%~dp0udp_forwarder.py"
pause
