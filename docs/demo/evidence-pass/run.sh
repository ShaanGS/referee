cd /tmp/referee/20261122-050048 || exit 1
LPID=""
KPID=""
./referee_load -t 0 -p 1 -d 1.0 -s 12.0 > keepawake.log 2>&1 &
KPID=$!
./referee_load -t 0 -p 20 -d 0.9 -s 12.0 > load.log 2>&1 &
LPID=$!
sudo -n ./referee_plant -n referee/controller -f 1000 -s 10.0 -P 40 -T 100 -g 2.0,40.0,0.001 -m 1.0,0.05,-10.0,10.0 -o samples.csv -M 120 -c 0 -D 200 > plant.json 2> plant.err
PRC=$?
[ -n "$LPID" ] && wait $LPID
[ -n "$KPID" ] && wait $KPID
echo "{\"plant_rc\": $PRC}" > run.json
