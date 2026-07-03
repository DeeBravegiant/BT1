### Title
Permissionless `FeeReceiver.sendFunds()` + `LRTOracle.updateRSETHPrice()` enables sandwich attack to steal MEV yield from rsETH holders - (File: contracts/FeeReceiver.sol)

---

### Summary

`FeeReceiver.sendFunds()` carries no access-control modifier, so any caller can flush all accumulated MEV/execution-layer rewards into `LRTDepositPool` in one atomic step. Because `LRTOracle.updateRSETHPrice()` is also permissionless (`public whenNotPaused`), an attacker can sandwich both calls — depositing ETH at the old (lower) rsETH price, triggering the reward flush and price update, then withdrawing at the new (higher) price — stealing a proportional share of MEV rewards that should accrue exclusively to existing rsETH holders.

---

### Finding Description

**Root cause — `FeeReceiver.sendFunds()` is permissionless and distributes rewards in one step:**

```solidity
// contracts/FeeReceiver.sol:53-58
function sendFunds() external {                          // ← no access control
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

`receiveFromRewardReceiver` is equally unrestricted — it simply accepts ETH and adds it to the deposit pool's balance:

```solidity
// contracts/LRTDepositPool.sol:61
function receiveFromRewardReceiver() external payable { }
``` [2](#0-1) 

**Root cause — `LRTOracle.updateRSETHPrice()` is permissionless:**

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

**How the price is computed — deposit pool ETH balance is included in TVL:**

`_getTotalEthInProtocol()` calls `getTotalAssetDeposits(ETH)`, which resolves to `getETHDistributionData()`, which reads `address(this).balance` of the deposit pool. The moment `sendFunds()` is called, the MEV rewards land in that balance and are immediately visible to the next `updateRSETHPrice()` call. [4](#0-3) 

The new price is then:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
``` [5](#0-4) 

**Deposit path (attacker entry):**

`LRTDepositPool.depositETH()` mints rsETH at the *stored* (pre-update) price with no deposit fee on L1:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

**Exit path (attacker exit):**

`LRTWithdrawalManager.instantWithdrawal()` burns rsETH and returns assets at the *current* oracle price (post-update), locking in the higher price at initiation time. The regular `initiateWithdrawal` path also locks in the price at initiation via `getExpectedAssetAmount`. [7](#0-6) 

---

### Impact Explanation

MEV/execution-layer rewards accumulated in `FeeReceiver` are yield that belongs to existing rsETH holders. Because the distribution is stepwise and permissionless, an attacker can dilute the existing supply by depositing immediately before the flush, capturing a share of the reward proportional to their injected capital. Existing holders receive less yield than they are entitled to. This is **theft of unclaimed yield** (High severity per the allowed impact scope).

---

### Likelihood Explanation

- Both `sendFunds()` and `updateRSETHPrice()` are callable by anyone with no preconditions.
- The attacker can execute the full sandwich atomically in a single transaction using a flash loan, eliminating market risk.
- `FeeReceiver` accumulates MEV rewards continuously; the attack becomes more profitable the longer rewards sit unflused.
- The only partial mitigation is `pricePercentageLimit` in `_updateRsETHPrice()`, which reverts for non-managers if the price jump exceeds the configured threshold. However: (a) if `pricePercentageLimit == 0` (disabled), there is no protection at all; (b) even with a limit, the attacker can still profit from smaller, sub-threshold reward batches; (c) the attacker can themselves set the timing to exploit periods when the limit is not binding. [8](#0-7) 

---

### Recommendation

1. **Add access control to `FeeReceiver.sendFunds()`** — restrict it to `MANAGER_ROLE` or a trusted keeper, so the timing of reward distribution cannot be weaponized by an attacker.
2. **Stream rewards over time** — instead of flushing the entire balance in one call, distribute rewards gradually (e.g., linearly over 24 hours) so no single block contains a large, exploitable price jump.
3. **Combine `sendFunds()` + `updateRSETHPrice()` atomically under access control** — if both must remain callable, ensure they are always executed together by a privileged account so the window between deposit and price update cannot be exploited.

---

### Proof of Concept

Assume:
- Current rsETH price `P1 = 1.05e18` (1.05 ETH per rsETH)
- Total rsETH supply `S = 100,000 rsETH`
- TVL `T1 = 105,000 ETH`
- `FeeReceiver` holds `R = 500 ETH` in accumulated MEV rewards
- `pricePercentageLimit = 0` (disabled) or the jump is within the limit

**Attack (single atomic transaction via flash loan):**

1. **Borrow** 50,000 ETH via flash loan.
2. **Call** `LRTDepositPool.depositETH{value: 50000 ETH}(0, "")`.
   - rsETH minted = 50,000 / 1.05 ≈ 47,619 rsETH
   - New supply = 147,619 rsETH; new TVL = 155,000 ETH
3. **Call** `FeeReceiver.sendFunds()`.
   - 500 ETH moves to deposit pool; TVL = 155,500 ETH
4. **Call** `LRTOracle.updateRSETHPrice()`.
   - `previousTVL = 147,619 * 1.05 ≈ 155,000 ETH`
   - `rewardAmount = 500 ETH`; `protocolFee ≈ 0` (or small)
   - `newRsETHPrice = 155,500 / 147,619 ≈ 1.05338e18`
5. **Call** `LRTWithdrawalManager.instantWithdrawal(ETH, 47619 rsETH, "")`.
   - ETH received = 47,619 * 1.05338 ≈ 50,161 ETH
6. **Repay** flash loan of 50,000 ETH.
7. **Profit ≈ 161 ETH** (≈ 32% of the 500 ETH MEV reward batch), stolen from the 100,000 legitimate rsETH holders who should have received the full 500 ETH.

The attack scales linearly with the size of the accumulated reward batch and the attacker's injected capital.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L60-61)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L479-480)
```text
    {
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-266)
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
            }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```
