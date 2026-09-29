#!/bin/bash
# NovaRouter Health Check Runner Script
# Run: bash /path/to/Novarouter/run_health_check.sh --cron

cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1

echo "=========================================="
echo "NovaRouter Health Check System"
echo "=========================================="
echo ""

if [ "$1" == "--cron" ] || [ "$1" == "-c" ]; then
    # Run cron job (non-interactive)
    echo "[$(date)] Running automatic health check..."
    python3 cron_health_check.py
    exit_code=$?
    
    if [ $exit_code -eq 0 ]; then
        echo "[$(date)] Health check completed successfully"
    else
        echo "[$(date)] Health check incomplete with exit code: $exit_code"
    fi
    
    exit $exit_code
elif [ "$1" == "--interactive" ] || [ "$1" == "-i" ]; then
    # Run interactive menu
    echo "Starting interactive health check menu..."
    python3 health_check_and_toggle.py
else
    # Show help
    echo "Usage:"
    echo "  $0 --cron    : Run automatic health check (for cron jobs)"
    echo "  $0 --interactive : Run interactive menu"
    echo ""
    echo "Quick commands:"
    echo "  python3 health_check_and_toggle.py  # Interactive menu"
    echo "  python3 cron_health_check.py        # Automatic check with logs"
    echo ""
    echo "Log file: $(pwd)/health_check.log"
fi
