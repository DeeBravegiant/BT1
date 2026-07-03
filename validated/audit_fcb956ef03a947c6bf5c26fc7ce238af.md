Audit Report

## Title
Permissionless `FeeReceiver.sendFunds()` Enables Yield Theft via Deposit-Before-Reward-Distribution — (File: `contracts/FeeReceiver.sol`)

## Summary
`FeeReceiver.sendFunds()` has no access control, allowing any caller to trigger reward distribution at will. Combined with the fact that `LRTOracle.updateRSETHPrice()` is also permissionless and the rsETH minting rate uses a stored (stale) price, an attacker can deposit at the pre-reward price, push accumulated rewards into the deposit pool, then update the price — capturing a portion of yield that belongs to existing rsETH holders.

## Finding Description
`FeeReceiver.sendFunds()` carries no role modifier and is callable by any EOA or contract: [1](#0-0) 

`LRTDepositPool.receiveFromRewardReceiver()` is a plain payable stub with no logic or access control: [2](#0-1) 

`LRTOracle.updateRSETHPrice()` is also permissionless (only `whenNotPaused`): [3](#0-2) 

The rsETH minting rate reads the stored `rsETHPrice` directly, which is only updated when `updateRSETHPrice()` is explicitly called: [4](#0-3) 

The TVL used to compute the new price includes `address(this).balance` of the deposit pool, which incorporates the just-pushed reward ETH: [5](#0-4) 

**Exploit path:**
1. Rewards R accumulate in `FeeReceiver`; stored `rsETHPrice` = P reflects TVL T (without R).
2. Attacker calls `depositETH{value: X}()` → mints X/P rsETH at the stale price.
3. Attacker calls `FeeReceiver.sendFunds()` → R ETH pushed to deposit pool; TVL becomes T + X + R.
4. Attacker calls `LRTOracle.updateRSETHPrice()` → new price = (T + X + R − fee) / (S + X/P).
5. Attacker's rsETH is now worth X + X·R·(1−fee_rate)/(T+X), a profit of X·R·(1−fee_rate)/(T+X).

**Why the `pricePercentageLimit` guard does not prevent this:**
The guard reverts if the price increase exceeds `pricePercentageLimit * highestRsethPrice`. The per-token price increase from the reward injection is R/(S + X/P). As X grows, this term shrinks toward zero. The attacker can always choose X large enough so the price increase falls below the threshold, bypassing the revert entirely: [6](#0-5) 

**Why the ETH deposit limit does not prevent this:**
The ETH deposit limit check only reverts if the existing total already exceeds the cap — it does not add the incoming deposit amount to the comparison, unlike the ERC-20 path: [7](#0-6) 

This means the attacker can deposit an arbitrarily large X in a single transaction as long as the current total is below the cap.

## Impact Explanation
**High — Theft of unclaimed yield.** Existing rsETH holders accumulate yield as MEV/execution-layer rewards flow into `FeeReceiver`. An attacker who front-runs the reward distribution captures a fraction X·R/(T+X) of those rewards. With X = T the attacker steals ~50% of all pending rewards; with X >> T the attacker approaches stealing nearly all of R. The loss is permanent and directly reduces the yield that legitimate holders would have received.

## Likelihood Explanation
All three calls (`depositETH`, `sendFunds`, `updateRSETHPrice`) are permissionless and executable by any EOA or contract in a single transaction or atomic bundle. The `FeeReceiver` balance is publicly visible on-chain. No privileged key, oracle compromise, or governance action is required. MEV rewards accumulate continuously, making this a recurring opportunity. The only practical barrier is the capital requirement for the deposit and the withdrawal delay to realize the profit, both of which are manageable for a well-capitalized attacker.

## Recommendation
1. **Add access control to `FeeReceiver.sendFunds()`** — restrict it to an authorized operator/manager role so that reward distribution timing cannot be controlled by an attacker.
2. **Alternatively**, call `updateRSETHPrice()` atomically at the start of `depositETH()` and `depositAsset()` so the price is always current before rsETH is minted, eliminating the stale-price window entirely.
3. Both mitigations together provide defense-in-depth.

## Proof of Concept
```
Initial state:
  TVL (deposit pool) = 1000 ETH
  rsETH supply S = 1000
  rsETHPrice P = 1e18 (1 ETH per rsETH)
  FeeReceiver.balance R = 10 ETH (accumulated MEV rewards, not yet pushed)

Step 1: Attacker calls LRTDepositPool.depositETH{value: 1000 ETH}(0, "")
  getRsETHAmountToMint: 1000e18 * 1e18 / 1e18 = 1000 rsETH  (stale price used)
  Deposit pool balance: 2000 ETH, rsETH supply: 2000

Step 2: Attacker calls FeeReceiver.sendFunds()
  10 ETH transferred to deposit pool
  Deposit pool balance: 2010 ETH, rsETH supply: 2000

Step 3: Attacker calls LRTOracle.updateRSETHPrice()
  previousTVL = 2000 * 1e18 = 2000 ETH  (current supply × old price)
  totalETHInProtocol = 2010 ETH
  rewardAmount = 10 ETH → protocol fee taken on 10 ETH
  newRsETHPrice ≈ 2010e18 / 2000 = 1.005e18

Result:
  Attacker's 1000 rsETH × 1.005 = 1005 ETH  → profit ≈ 5 ETH
  Existing holders' 1000 rsETH × 1.005 = 1005 ETH  (gained only 5 ETH)
  Without attack, existing holders would have gained ~10 ETH
  → Attacker stole ~5 ETH of yield from existing rsETH holders

Foundry fork test plan:
  1. Fork mainnet at a block where FeeReceiver.balance > 0.
  2. Record existing holder rsETH balance and rsETHPrice.
  3. Execute the three-step sequence above from an attacker address.
  4. Assert attacker's ETH-equivalent gain > 0 and existing holder gain < R.
  5. Fuzz over X to show profit scales with deposit size.
```

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L678-679)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-265)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```
