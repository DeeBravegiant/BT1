Looking at the vulnerability class — a function computing "available/active" status that fails to check a "paused/disabled" state flag — I need to find an analog in Cardano Ledger where a ratification or accounting function omits a relevant state check.

The key analog is in `dRepAcceptedRatio` in the Conway RATIFY rule.