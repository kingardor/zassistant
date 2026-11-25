#!/bin/bash
# Container entrypoint.
#
# The web UI starts first, so the browser can show the startup screen while
# Boost connects and the model loads. Each step reports its progress to the
# z:status hash in Redis; the LLM worker (zassistant.py) reports the rest.

MODEL=${MODEL:-llama3.1:8b}
PORT=${PORT:-8080}
SESSION_TIMEOUT=${BOOST_SESSION_TIMEOUT:-3600}

# Start redis
redis-server --daemonize yes
until redis-cli ping >/dev/null 2>&1; do sleep 0.2; done

status() { redis-cli hset z:status "$@" >/dev/null; }
now_ms() { date +%s%3N; }
begin()  { status "stage.$1" running "stage.$1.t0" "$(now_ms)" ${2:+"stage.$1.detail" "$2"}; }
finish() {
  local t0
  t0=$(redis-cli hget z:status "stage.$1.t0")
  status "stage.$1" done "stage.$1.ms" $(( $(now_ms) - t0 )) ${2:+"stage.$1.detail" "$2"}
}
fail()   { status "stage.$1" error error "$2"; }

if [ "$BOOST" == 1 ]; then MODE=boost; else MODE=local; fi

redis-cli del z:status z:jobs >/dev/null
status mode "$MODE" model "$MODEL" gpu_id "${BOOST_GPU_ID:-}" \
       session_timeout "$SESSION_TIMEOUT" booted_at "$(date +%s)"
for s in session ollama model index; do status "stage.$s" pending; done

# Web UI
echo "Starting web UI on port $PORT.."
python3 server.py --host "${HOST:-127.0.0.1}" --port "$PORT" &
UI_PID=$!

if [ "$MODE" == boost ]; then
  echo "Using Boost.."
  begin session "GPU ${BOOST_GPU_ID:0:8}…"
  if /cf/hpcf session request \
      --gpu-ids "$BOOST_GPU_ID" \
      --session-timeout "$SESSION_TIMEOUT"; then
    status session_started "$(date +%s)"
    finish session "GPU ${BOOST_GPU_ID:0:8}… · $(( SESSION_TIMEOUT / 60 )) min"
  else
    fail session "Couldn't get a Boost session for GPU $BOOST_GPU_ID. Check that the Z host is online and sharing it."
    wait $UI_PID
    exit 1
  fi

  echo "Starting Ollama server..."
  begin ollama "via hpcf run"
  OLLAMA_LOAD_TIMEOUT=1h OLLAMA_KEEP_ALIVE=1h /cf/hpcf \
  run ollama serve &
else
  echo "Using local GPU.."
  status stage.session skipped
  begin ollama "local GPU"
  ollama serve &
fi

sleep 2

echo "Waiting for Ollama server to be active..."
while [ "$(ollama list | grep 'NAME')" == "" ]; do
  sleep 1
done
finish ollama

if [ "$MODE" == boost ]; then
  /cf/hpcf run python3 zassistant.py
else
  python3 zassistant.py
fi

# Keep the UI up (showing any startup error) if the worker exits
wait $UI_PID
