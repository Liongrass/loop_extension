#!/usr/bin/env bash
# Regenerate loop.desc from loop.proto. Any protoc version works: a descriptor
# set doesn't depend on the protobuf runtime version LNbits ships.
set -euo pipefail
cd "$(dirname "$0")"
protoc -I . --descriptor_set_out=loop.desc loop.proto
echo "generated loop.desc"
