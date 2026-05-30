#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT="${GEEKDESK_CONTROL_PROJECT:-$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [ -d "${PROJECT_ROOT}/.venv/lib" ]; then
  for site_packages in "${PROJECT_ROOT}"/.venv/lib/python*/site-packages; do
    if [ -d "${site_packages}" ]; then
      export PYTHONPATH="${site_packages}:${PYTHONPATH}"
    fi
  done
fi

exec /usr/bin/python3 -m ikea_desk_automation.geekdesk_control "$@"
