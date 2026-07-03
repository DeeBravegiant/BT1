Audit Report

## Title
Stale `rsETHPrice` Allows New Depositors to Capture Accrued Yield from Existing Holders - (`contracts/LRTOracle.sol` / `contracts/LRTDepositPool.sol`)

## Summary
`LRTOracle.rsETHPrice` is a stored state variable updated only via explicit calls to `updateRSETHPrice()`. The deposit flow in `LRTDepositPool` reads this stored value directly with no staleness check and no forced refresh. When yield accrues (stETH daily rebases, EigenLayer rewards) and `rsETHPrice` lags behind the true TVL-derived price, new depositors receive excess rsETH, permanently diluting the yield that belongs to existing holders.

## Finding Description
`getRsETHAmountToMint()` computes the rsETH to mint as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.rsETHPrice()` is a stored state variable, not a live computation:

```solidity
uint256 public override rsETHPrice;
``` [2](#0-1) 

It is only updated when `updateRSETHPrice()` or `updateRSETHPriceAsManager()` is explicitly invoked:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

The deposit entry points `depositETH()` and `depositAsset()` both call `_beforeDeposit()`, which calls `getRsETHAmountToMint()` — a `private view` function that reads the stored price with no staleness check and no forced price refresh:

```solidity
function _beforeDeposit(...) private view returns (uint256 rsethAmountToMint) {
    ...
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    ...
}
``` [4](#0-3) 

`_updateRsETHPrice()` computes the true price from live TVL across the deposit pool, node delegators, EigenLayer strategies, unstaking vault, and converter — all of which grow as yield accrues:

```solidity
uint256 totalETHInProtocol = _getTotalEthInProtocol();
...
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
rsETHPrice = newRsETHPrice;
``` [5](#0-4) 

There is no on-chain mechanism that enforces `updateRSETHPrice()` is called before any deposit is processed. The gap between yield accrual and the next price update is the exploitable window.

Additionally, `_updateRsETHPrice()` contains a `PriceAboveDailyThreshold` revert for non-manager callers when the price increase exceeds `pricePercentageLimit`:

```solidity
if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
    revert PriceAboveDailyThreshold();
}
``` [6](#0-5) 

This means that after a large yield event, the public `updateRSETHPrice()` call itself reverts, forcing the staleness window to persist until a manager acts — widening the attack surface further.

## Impact Explanation
When yield accrues and `rsETHPrice` is stale (lower than the true price), a depositor receives `amount / rsETHPrice_stale` rsETH — more than their proportional share of the pool. When `updateRSETHPrice()` is eventually called, the new price is computed as `totalETH / totalSupply`. Because excess rsETH was minted at the stale price, the updated price is lower than it would have been, permanently reducing the redemption value of every existing holder's rsETH. This is a concrete, on-chain transfer of accrued yield from existing holders to the new depositor. Impact: **High — Theft of unclaimed yield**. [7](#0-6) 

## Likelihood Explanation
stETH rebases daily (~4–5% APY, ~0.011–0.014% per day). Any gap between a rebase event and the next `updateRSETHPrice()` call creates an exploitable window. No privileged access is required — any address can call `depositETH()` or `depositAsset()`. A sophisticated depositor can monitor on-chain TVL growth (via `getTotalAssetDeposits()` and `getAssetPrice()`) and time their deposit to the staleness window. The attack is repeatable every rebase cycle. Likelihood: **Medium**. [8](#0-7) 

## Recommendation
Atomically call `_updateRsETHPrice()` (or an equivalent internal refresh) at the start of `depositETH()` and `depositAsset()` before computing `rsethAmountToMint`. This ensures the price used for minting always reflects the current TVL, eliminating the staleness window entirely.

Alternatively, enforce a maximum staleness bound by storing a `lastPriceUpdateTimestamp` in `LRTOracle` and reverting in `getRsETHAmountToMint()` if the price is older than a configurable threshold (e.g., 1 hour). [3](#0-2) 

## Proof of Concept
1. Protocol holds 1000 stETH; `rsETHPrice` = 1.05 ETH (last updated 24 hours ago). Existing rsETH supply = ~952.38 rsETH.
2. stETH rebases: protocol TVL is now 1010 stETH worth of ETH. True `rsETHPrice` ≈ 1.0605 ETH. Stored `rsETHPrice` is still 1.05.
3. Attacker calls `depositETH{value: 100 ether}(0, "")`. `getRsETHAmountToMint` computes `100e18 / 1.05e18 ≈ 95.238 rsETH`. Correct amount would be `100e18 / 1.0605e18 ≈ 94.298 rsETH`. Attacker receives ~0.94 excess rsETH.
4. Anyone calls `updateRSETHPrice()`. New price = `(1010 + 100) ETH / (952.38 + 95.238) rsETH ≈ 1.0596 ETH` instead of the correct `≈ 1.0605 ETH` had the correct amount been minted.
5. All prior holders' rsETH is now worth less; the ~0.94 rsETH of yield that belonged to them has been captured by the attacker.

Foundry fork test plan: fork mainnet, set `rsETHPrice` to a value 1% below the live TVL-derived price (simulating a 24-hour stale window), call `depositETH` as an unprivileged address, then call `updateRSETHPrice`, and assert that the resulting `rsETHPrice` is lower than it would have been if the correct rsETH amount had been minted. [9](#0-8)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-250)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L262-265)
```text
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```
