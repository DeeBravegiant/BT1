### Title
Permissionless `FeeReceiver.sendFunds()` + `LRTOracle.updateRSETHPrice()` Enables Front-Running Yield Theft from rsETH Holders - (`contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`FeeReceiver.sendFunds()` carries no access control and can be called by any external account at any time. Combined with the equally permissionless `LRTOracle.updateRSETHPrice()`, an attacker can deposit ETH into `LRTDepositPool`, immediately trigger reward distribution and a price update, then withdraw at the inflated rsETH price — capturing yield that should have accrued exclusively to pre-existing rsETH holders.

---

### Finding Description

`FeeReceiver` accumulates MEV and execution-layer rewards passively. The function that flushes those rewards into the deposit pool is unrestricted: [1](#0-0) 

```solidity
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Sending those funds to the deposit pool increases `totalETHInProtocol` as measured by `LRTOracle._getTotalEthInProtocol()`, which sums `address(this).balance` of the deposit pool and all node delegators. [2](#0-1) 

The oracle price update is also permissionless: [3](#0-2) 

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` computes `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply`. After `sendFunds()` inflates the numerator, the price rises and is written to `rsETHPrice`. [4](#0-3) 

Withdrawals (both queued and instant) use `lrtOracle.rsETHPrice()` to compute the ETH payout: [5](#0-4) 

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

---

### Impact Explanation

Let `T` = total ETH in protocol before the attack, `S` = rsETH supply, `R` = rewards sitting in `FeeReceiver`, `A` = attacker deposit.

| Step | State |
|---|---|
| Attacker deposits `A` ETH | Gets `A·S/T` rsETH at price `T/S` |
| `sendFunds()` called | TVL becomes `T + A + R` |
| `updateRSETHPrice()` called | New price ≈ `(T+A+R)/(S + A·S/T)` |
| Attacker withdraws | Receives `A + A·R/(T+A)` ETH |
| **Attacker profit** | **`A·R/(T+A)`** |
| **Existing holders' loss** | **`R·A/(T+A)`** (their share of `R` diluted by attacker) |

The attacker extracts a portion of the pending reward `R` that should have accrued only to holders who were present before the reward event. This is a direct theft of unclaimed yield.

When `isInstantWithdrawalEnabled` is `true` for an asset, the attack collapses to a near-atomic sequence (deposit → sendFunds → updateRSETHPrice → instantWithdrawal), eliminating the 8-day capital lock-up risk entirely. [6](#0-5) 

---

### Likelihood Explanation

- `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are both callable by any EOA or contract with no preconditions beyond the contract not being paused.
- MEV bots routinely monitor mempool and on-chain state for exactly this pattern.
- The attack is profitable whenever `A·R/(T+A)` exceeds the cost of capital for the lock-up period (or the instant-withdrawal fee when that path is open).
- No privileged access, leaked keys, or oracle compromise is required.

---

### Recommendation

1. **Restrict `FeeReceiver.sendFunds()`** to a trusted role (e.g., `MANAGER` or `OPERATOR`), or add a time-lock / minimum accumulation threshold before funds can be flushed. [1](#0-0) 

2. **Restrict `LRTOracle.updateRSETHPrice()`** to a keeper/manager role, or enforce a minimum time between successive price updates (e.g., 1 hour), so that a deposit immediately followed by a price update cannot be executed atomically. [3](#0-2) 

3. Consider a **snapshot-based yield accrual** model where only holders present at the start of a reward epoch receive that epoch's yield, preventing late depositors from capturing pending rewards.

---

### Proof of Concept

```
// Assume FeeReceiver holds 50 ETH in accumulated MEV rewards.
// Protocol TVL = 10,000 ETH, rsETH supply = 9,500 (price ≈ 1.0526 ETH/rsETH).

1. Attacker calls LRTDepositPool.depositETH{value: 5000 ETH}(0, "")
   → Receives 5000 / 1.0526 ≈ 4750 rsETH

2. Attacker calls FeeReceiver.sendFunds()
   → 50 ETH transferred to DepositPool; TVL becomes 15,050 ETH

3. Attacker calls LRTOracle.updateRSETHPrice()
   → new price = 15,050 / (9,500 + 4,750) ≈ 1.0561 ETH/rsETH

4a. [If instant withdrawal enabled]
    Attacker calls LRTWithdrawalManager.instantWithdrawal(ETH, 4750 rsETH, "")
    → receives 4750 × 1.0561 ≈ 5016.6 ETH (minus small instant fee)
    → profit ≈ 16.6 ETH stolen from existing holders in one block

4b. [Standard path]
    Attacker calls initiateWithdrawal(ETH, 4750 rsETH, "")
    → after 8-day delay, receives ≈ 5016.6 ETH
    → profit ≈ 16.6 ETH; existing holders lose their proportional share of the 50 ETH reward
```

### Citations

**File:** contracts/FeeReceiver.sol (L53-57)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L331-348)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
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

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
