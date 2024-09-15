#!/bin/bash

# Start redis
redis-server --daemonize yes

if [ $BOOST == 0 ]; then
  echo "Using local GPU.."
  ollama serve &

else
  echo "Using Boost.."
  
  /cf/hpcf session request \
  --gpu-ids $BOOST_GPU_ID \
  --session-timeout 3600
  
  echo "Starting Ollama server..."
  OLLAMA_LOAD_TIMEOUT=1h OLLAMA_KEEP_ALIVE=1h /cf/hpcf \
  run ollama serve &
fi

sleep 2

echo "Waiting for Ollama server to be active..."
while [ "$(ollama list | grep 'NAME')" == "" ]; do
  sleep 1
done

if [ $BOOST == 1 ]; then
  /cf/hpcf run python3 zassistant.py
else
  python3 zassistant.py
fi