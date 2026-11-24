set -e
cd /c/Users/you/Desktop/TradeDeck/tools; L=../specs/lab
python v3b_book.py > $L/v3b_book.log 2>&1
python v3b_build.py > $L/v3b_build.log 2>&1
python v3b_model.py > $L/v3b_learn.log 2>&1
V3_PLACEBO=1 python v3b_model.py > $L/v3b_placebo.log 2>&1
python v3b_agree.py learn > $L/v3b_learn_trade.log 2>&1
echo ALLDONE >> $L/v3b_learn_trade.log
