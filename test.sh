#! /bin/bash
set -euo pipefail
cd /app

./__main__.py \
    header /src/ \
    | tee /app/test.out
