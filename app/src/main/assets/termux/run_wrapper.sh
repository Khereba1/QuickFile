#!/bin/bash
# QuickFile <-> Termux bridge. Runs inside the job folder (same path logic in Termux and in proot-distro).
cd "$(dirname "$0")" || exit 1
echo started > started.txt
mkdir -p out
(
@BODY@
) > log.txt 2>&1
echo $? > done.txt
