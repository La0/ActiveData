export PYTHONPATH=.

python active_data/app.py --settings=tests/travis/app.json &
sleep 5

export TEST_CONFIG=tests/travis/app.json
python -m unittest discover tests --failfast
exit
