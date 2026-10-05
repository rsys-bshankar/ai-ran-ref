#!/bin/bash -eu
# Builds every fuzz/fuzz_*.py target (atheris) for ClusterFuzzLite.
# Dependencies come from the same hashed lock the services use.
cd "$SRC/ai-ran-smo/smo"
pip3 install --require-hashes -r requirements/runtime.txt
pip3 install --no-deps ./shared

for fuzzer in fuzz/fuzz_*.py; do
  compile_python_fuzzer "$fuzzer" --paths shared --paths onboarding
done
