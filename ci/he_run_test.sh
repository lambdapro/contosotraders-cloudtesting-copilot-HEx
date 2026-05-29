#!/usr/bin/env bash
# HyperExecute test-stage runner.
# HyperExecute may not keep a pre-stage background process alive into the test
# stage, so we ensure the Contoso app is up HERE (same stage as the test), then
# run the single pytest node HE hands us. he_launch_app.sh is idempotent — it
# returns immediately if the app is already running from the pre: stage.
set -uo pipefail

TEST_NODE="$1"

echo "[he_run_test] ensuring app is up before test: $TEST_NODE"
bash ci/he_launch_app.sh || { echo "[he_run_test] app failed to start"; exit 1; }

echo "[he_run_test] running: $TEST_NODE"
PYTHONPATH=. pytest "$TEST_NODE" -v --tb=short -s \
  --html=reports/report.html --junitxml=reports/junit.xml
