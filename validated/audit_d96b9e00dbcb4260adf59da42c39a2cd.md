### Title
Zero rsETH Minted for Non-Zero Deposit Due to Integer Division Truncation — (`contracts/LRTDepositPool.sol`)

### Summary

`getRsETHAmountToMint` computes the rsETH amount using plain integer division. When `rsETHPrice` exceeds `depositAmount * assetPrice`, the result truncates to zero. With `minRSETHAmountExpected = 0` and `minAmountToDeposit = 0` (default), the deposit succeeds and the depositor receives no rsETH.

### Finding Description

The mint calculation in `getRsETHAmountToMint` is:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

For ETH with `assetPrice = 1e18`, `depositAmount = 1 wei`, and `rsETHPrice = 2e18`:

```
rsethAmountToMint = (1 * 1e18) / 2e18 = 0  (Solidity integer truncation)
```

Back in `_beforeDeposit`, the only slippage guard is:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [2](#0-1) 

With `minRSETHAmountExpected = 0`, the check `0 < 0` is `false` — no revert. The deposit amount guard is:

```solidity
if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
    revert InvalidAmountToDeposit();
}
``` [3](#0-2) 

`minAmountToDeposit` is an uninitialized `uint256` that defaults to `0`, so `1 < 0` is also `false`. The call then reaches `_mintRsETH(0)`:

```solidity
IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
``` [4](#0-3) 

In `RSETH.mint`, the `checkDailyMintLimit(0)` modifier does not revert for a zero amount (it only reverts if `currentPeriodMintedAmount + amount > maxMintAmountPerDay`, which is not triggered by adding 0): [5](#0-4) 

OpenZeppelin's `_mint(to, 0)` does not revert. The transaction completes: ETH is absorbed into the pool, 0 rsETH is minted to the depositor.

### Impact Explanation

The depositor's ETH is permanently absorbed into the pool (increasing TVL for all existing rsETH holders) while the depositor receives zero rsETH and has no claim on their deposit. The protocol does not lose value — it gains it — but the depositor fails to receive the promised return. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value**.

The threshold for triggering this is `depositAmount < rsETHPrice / assetPrice`. With rsETHPrice at 1.001e18 (a mere 0.1% above peg), any ETH deposit below 1001 wei yields 0 rsETH. As the protocol matures and rsETHPrice grows, the threshold grows proportionally.

### Likelihood Explanation

- `minAmountToDeposit` is `0` by default and requires an explicit admin call to set it.
- `rsETHPrice` naturally exceeds `1e18` as staking rewards accumulate — this is the intended, expected behavior of the protocol.
- A user who passes `minRSETHAmountExpected = 0` (either naively or via a poorly written integration) receives no protection.
- No role compromise, oracle manipulation, or external dependency failure is required. The path is reachable on unmodified production code.

### Recommendation

Add an explicit zero-output guard in `_beforeDeposit`:

```solidity
if (rsethAmountToMint == 0) {
    revert ZeroRsETHMinted();
}
```

This should be placed after the `getRsETHAmountToMint` call and before the `minRSETHAmountExpected` check. Additionally, enforce a non-zero `minAmountToDeposit` during initialization to prevent dust deposits.

### Proof of Concept

```solidity
// Preconditions:
// 1. rsETHPrice has grown to 2e18 (natural after staking rewards)
// 2. minAmountToDeposit == 0 (default)

// Call:
depositPool.depositETH{value: 1}(0, "");

// Trace:
// _beforeDeposit(ETH_TOKEN, 1, 0)
//   depositAmount=1 > 0, 1 >= minAmountToDeposit(0) → passes
//   getRsETHAmountToMint(ETH_TOKEN, 1)
//     = (1 * 1e18) / 2e18 = 0
//   rsethAmountToMint=0 >= minRSETHAmountExpected=0 → passes
// _mintRsETH(0) → RSETH.mint(msg.sender, 0) → _mint(msg.sender, 0) → succeeds
// Result: 1 wei ETH absorbed into pool, 0 rsETH minted to depositor
// Assert: rsETH.balanceOf(depositor) == 0  ✓ (invariant broken)
``` [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L688-689)
```text
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
```

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```
