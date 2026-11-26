#!/usr/bin/env bash
# Build the Lambda bundle. Reproducible, because the previous bundle was assembled by hand
# and the commands lived only in a shell history.
#
# The wheels must be Linux x86_64 for CPython 3.12, which is the Lambda runtime - a wheel
# built on this Mac will import fine here and fail in the cloud with no useful error.
set -euo pipefail
cd "$(dirname "$0")/.."
BUILD=deploy/build
rm -rf "$BUILD" && mkdir -p "$BUILD"

python3 -m pip install -q \
  --platform manylinux2014_x86_64 --python-version 3.12 \
  --implementation cp --only-binary=:all: --upgrade \
  --target "$BUILD" \
  fastapi mangum pydantic "psycopg[binary]"

# boto3 is already in the Lambda runtime, so it is deliberately not vendored.
cp -R src/setuhaul "$BUILD/setuhaul"
mkdir -p "$BUILD/app" && cp app/*.py "$BUILD/app/"
cp deploy/lambda_handler.py deploy/build_workdb_shim.py deploy/ddl.sql "$BUILD/"

# Strip what only bloats the upload.
find "$BUILD" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -name "*.dist-info" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -name "tests" -type d -prune -exec rm -rf {} + 2>/dev/null || true

( cd "$BUILD" && zip -qr ../bundle.zip . )
ls -lh deploy/bundle.zip
