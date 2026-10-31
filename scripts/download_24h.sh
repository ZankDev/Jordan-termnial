#!/bin/sh
# resumable: each pass fills what the previous one missed (Dukascopy rate-limits, so some hours fail transiently)
cd "C:/Users/yarde/Desktop/Real algo trader - nostro"
for pass in 1 2 3 4; do
  echo "=== pass $pass $(date)"
  .venv/Scripts/python.exe -W ignore -u -m app.data.overnight_source download ES 60
  .venv/Scripts/python.exe -W ignore -u -m app.data.overnight_source download NQ 60
  sleep 600
done
echo ALLDONE
