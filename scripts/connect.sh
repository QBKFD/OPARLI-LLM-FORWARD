#!/bin/bash
# Auto-connect to remote server for algo trading

SERVER_HOST="${OPARLI_SERVER_HOST:-your-server-ip}"
SSH_KEY="${OPARLI_SSH_KEY:-~/.ssh/id_rsa}"

# Check if tunnel already exists
if ! pgrep -f "ssh.*8001:localhost:8001.*${SERVER_HOST}" > /dev/null; then
    echo "Establishing SSH tunnel to server..."
    ssh -i "$SSH_KEY" -L 8001:localhost:8001 -N -f "ubuntu@${SERVER_HOST}"
    echo "✓ SSH tunnel established"
else
    echo "✓ SSH tunnel already active"
fi

# Check if streaming is running
echo "Checking streaming service..."
RESPONSE=$(curl -s "http://localhost:8001/api/streaming/start" -X POST 2>/dev/null)

if echo "$RESPONSE" | grep -q '"connected":true'; then
    echo "✓ Streaming service connected to TWS"
    echo "✓ Data flowing continuously"
else
    echo "⚠ TWS connection issue - check IB Gateway on server"
fi

echo ""
echo "Frontend: http://localhost:5173"
echo "API: http://localhost:8001"
