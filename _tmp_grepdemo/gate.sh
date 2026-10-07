set -euo pipefail
log="$1"
failed=0
if grep -q '^REGRESSION ' "$log" 2>/dev/null; then
  echo "::error::benchmark regression detected in $(basename "$log"):"
  grep '^REGRESSION ' "$log" | sed 's/^/::error::/'
  failed=1
fi
if [ "$failed" -ne 0 ]; then
  echo "::error::nightly benchmark gate FAILED (regression beyond 25% and 20ms)."
  exit 1
fi
echo "nightly benchmark gate passed: no regression beyond tolerance."