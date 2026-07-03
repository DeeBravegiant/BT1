### Title
First Depositor rsETH Share Inflation via Direct ETH Donation Causes Victim Deposits to Mint Zero rsETH - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

### Summary
An unprivileged attacker who is the first depositor can donate ETH directly to `LRTDepositPool` to inflate `totalETHInProtocol`, then trigger `updateRSETHPrice()` to push `rsETHPrice` to an astronomically high value. Subsequent depositors who pass `minRSETHAmountExpected = 0` receive 0 rsETH for their full deposit, while the attacker—holding all outstanding rsETH—redeems the inflated balance and steals the victim's funds.

### Finding Description

**Step 1 — Attacker is the first depositor.**

`LRTDepositPool.depositETH()` calls `_beforeDeposit()`, which calls `getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`rsETHPrice` is initialized to `1 ether` when `rsethSupply == 0`:

```solidity
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
```

The `minAmountToDeposit` storage variable is never set in `initialize()`, so it defaults to `0`. The only guard is `depositAmount == 0`, meaning a deposit of **1 wei** passes. The attacker deposits 1 wei ETH and receives 1 wei rsETH.

**Step 2 — Attacker donates ETH directly to inflate TVL.**

`LRTDepositPool` has an unrestricted `receive()` function:

```solidity
receive() external payable { }
```

`getETHDistributionData()` counts `address(this).balance` as part of the protocol TVL:

```solidity
ethLyingInDepositPool = address(this).balance;
```

The attacker sends a large amount of ETH (e.g., 1 000 000 ETH) directly to the contract. This ETH is now counted in `totalETHInProtocol` but no new rsETH is minted.

**Step 3 — Attacker triggers `updateRSETHPrice()`.**

`updateRSETHPrice()` is a public, permissionless function:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Inside `_updateRsETHPrice()`:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

With `totalETHInProtocol ≈ 1 000 000 ETH` and `rsethSupply = 1 wei`, `newRsETHPrice ≈ 1e42`. The price-increase guard is:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

`pricePercentageLimit` is **never set in `initialize()`**, so it defaults to `0`. The condition short-circuits to `false`, and the guard is completely bypassed. `rsETHPrice` is updated to the inflated value.

**Step 4 — Victim deposits and receives 0 rsETH.**

The victim calls `depositETH(0, "")` (passing `minRSETHAmountExpected = 0`, a common default). `getRsETHAmountToMint` computes:

```solidity
rsethAmountToMint = (500_000 ETH * 1e18) / 1e42 = 0  // integer division truncates
```

The slippage guard:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

evaluates `0 < 0` → `false`, so it does **not** revert. The victim's 500 000 ETH is transferred into the protocol and 0 rsETH is minted.

**Step 5 — Attacker redeems.**

The attacker holds the only 1 wei of rsETH. After the victim's deposit, `totalETHInProtocol ≈ 1 500 001 ETH` and `rsethSupply = 1 wei`. The attacker redeems their 1 wei rsETH and recovers ~1 500 001 ETH, stealing the victim's 500 000 ETH.

### Impact Explanation

**Critical — Direct theft of user funds.**

Any depositor who passes `minRSETHAmountExpected = 0` (the natural default when no slippage protection is applied) loses their entire deposit. The attacker recovers both their donation and the victim's funds. The attack is self-financing: the donation is returned in full plus the stolen amount.

### Likelihood Explanation

**High** during the window between deployment and admin configuration:

- `pricePercentageLimit` defaults to `0` and is not set in `initialize()`, leaving the price-increase guard permanently disabled until an admin calls `setPricePercentageLimit()`.
- `minAmountToDeposit` defaults to `0`, allowing a 1-wei first deposit.
- `updateRSETHPrice()` is public and permissionless.
- `LRTDepositPool.receive()` is unrestricted.
- Many integrations and front-ends pass `minRSETHAmountExpected = 0` or omit slippage protection.

The attack requires no privileged access and can be executed atomically in a single block.

### Recommendation

1. **Set `pricePercentageLimit` in `initialize()`** to a safe non-zero value (e.g., 1% = `1e16`) so the price-increase guard is active from the first block.
2. **Set `minAmountToDeposit` in `initialize()`** to a meaningful floor (e.g., `0.001 ether`) to prevent 1-wei first deposits.
3. **Reject zero-rsETH mints** in `_beforeDeposit`: add `if (rsethAmountToMint == 0) revert ZeroRsETHMinted();` before the slippage check.
4. **Document that `minRSETHAmountExpected = 0` is unsafe** and enforce a non-zero minimum in the UI and any integration.

### Proof of Concept

```
Initial state: rsETHPrice = 1e18, rsethSupply = 0, totalETHInProtocol = 0

1. Attacker calls depositETH{value: 1}(0, "")
   → rsethAmountToMint = (1 * 1e18) / 1e18 = 1
   → rsethSupply = 1 wei, totalETHInProtocol = 1 wei

2. Attacker sends 1_000_000 ether directly to LRTDepositPool
   → address(LRTDepositPool).balance = 1_000_000 ether + 1 wei
   → totalETHInProtocol = 1_000_000 ether + 1 wei (via getETHDistributionData)

3. Attacker calls updateRSETHPrice()
   → newRsETHPrice = (1_000_000e18 + 1) * 1e18 / 1 ≈ 1e42
   → pricePercentageLimit == 0 → guard bypassed
   → rsETHPrice = 1e42

4. Victim calls depositETH{value: 500_000 ether}(0, "")
   → rsethAmountToMint = (500_000e18 * 1e18) / 1e42 = 0
   → 0 < 0 is false → no revert
   → Victim loses 500_000 ETH, receives 0 rsETH

5. Attacker redeems 1 wei rsETH
   → totalETHInProtocol ≈ 1_500_001 ETH, rsethSupply = 1 wei
   → Attacker recovers ≈ 1_500_001 ETH
   → Net profit: 500_000 ETH stolen from victim
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L56-58)
```text
    //////////////////////////////////////////////////////////////*/

    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L467-500)
```text
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L250-266)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
            }
```
