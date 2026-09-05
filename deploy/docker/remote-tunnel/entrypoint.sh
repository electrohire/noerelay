#!/bin/sh
set -eu
: "${SSH_HOST:?SSH_HOST is required}"
: "${SSH_USER:?SSH_USER is required}"
: "${REMOTE_HOST:?REMOTE_HOST is required}"
: "${REMOTE_PORT:?REMOTE_PORT is required}"
SSH_PORT="${SSH_PORT:-22}"
LOCAL_PORT="${LOCAL_PORT:-4001}"
install -m 0600 /run/input/id_remote_gpu /run/ssh/id_remote_gpu
install -m 0600 /run/input/known_hosts /run/ssh/known_hosts
export AUTOSSH_GATETIME=0 AUTOSSH_POLL=15
exec autossh -M 0 -N -g \
  -L "0.0.0.0:${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}" \
  -p "${SSH_PORT}" -i /run/ssh/id_remote_gpu \
  -o BatchMode=yes -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/ssh/known_hosts \
  "${SSH_USER}@${SSH_HOST}"
