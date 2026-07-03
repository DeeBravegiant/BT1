### Title
Sandwich Attack on `LRTOracle.updateRSETHPrice()` Enables Yield Theft via `instantWithdrawal` — (File: contracts/LRTOracle.sol, contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a permissionless public function that updates the stored `rsETHPrice` to reflect accrued restaking rewards. Because `LRTDepositPool.depositETH()` mints rsETH at the current stored price and `LRTWithdrawalManager.instantWithdrawal()` redeems rsETH at the current stored price with no block-delay requirement, an attacker can sandwich any `updateRSETHPrice()` transaction visible in the public mempool to capture the entire yield increment in a single atomic sequence.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is declared `public whenNotPaused` with no access restriction:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` computes `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply` and writes it to the storage variable `rsETHPrice`. When EigenLayer restaking rewards accrue, `totalETHInProtocol` grows, so the next call to `updateRSETHPrice()` increases `rsETHPrice`.

`LRTDepositPool.depositETH()` mints rsETH proportional to the **current stored** `rsETHPrice`:

```solidity
// contracts/LRTDepositPool.sol:76-93
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused ...
{
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    ...
}
```

`LRTWithdrawalManager.instantWithdrawal()` redeems rsETH at the **current stored** `rsETHPrice` with **no block-delay check**:

```solidity
// contracts/LRTWithdrawalManager.sol:212-253
function instantWithdrawal(address asset, uint256 rsETHUnstaked, string calldata referralId)
    external nonReentrant whenNotPaused onlySupportedAsset(asset) onlySupportedStrategy(asset)
    onlyInstantWithdrawalAllowed(asset)
{
    uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
    IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
    ...
    _transferAsset(asset, msg.sender, userAmount);
}
```

The regular withdrawal queue enforces `withdrawalDelayBlocks`, but `instantWithdrawal` has no such guard. An attacker can therefore deposit and instantly withdraw within the same block.

**Attack sequence:**

1. **Front-run**: Attacker observes a pending `updateRSETHPrice()` transaction in the public mempool. Attacker submits `depositETH{value: X}()` with higher gas, minting `X / P_old` rsETH at the stale (lower) price `P_old`.
2. **Target tx executes**: `updateRSETHPrice()` runs, setting `rsETHPrice = P_new > P_old`.
3. **Back-run**: Attacker calls `instantWithdrawal()`, redeeming `X / P_old` rsETH at `P_new`, receiving `(X / P_old) * P_new = X * (P_new / P_old)` ETH.
4. **Profit**: `X * (P_new / P_old - 1)` ETH — the entire yield increment captured from legitimate stakers.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every time `updateRSETHPrice()` is called to reflect accrued EigenLayer restaking rewards, an attacker can capture the full yield increment that should have been distributed proportionally to all existing rsETH holders. Legitimate long-term stakers receive zero yield for that period. The attack is repeatable on every price-update cycle, making it a persistent drain on protocol yield.

---

### Likelihood Explanation

**Medium.**

- `updateRSETHPrice()` is permissionless and is expected to be called regularly (by bots or anyone) from the public mempool.
- `instantWithdrawal` is a manager-enabled feature (`isInstantWithdrawalEnabled[asset]`). When enabled, the attack path is fully open.
- The `pricePercentageLimit` guard in `_updateRsETHPrice()` limits how large a single price jump can be for non-manager callers, but the attacker is sandwiching the *operator's* call, not calling `updateRSETHPrice()` themselves — so this guard does not protect against the attack.
- The `instantWithdrawalFee` (up to 10%) reduces profitability but does not eliminate it for large deposits.
- The `LRTUnstakingVault` must hold sufficient assets for instant withdrawal, which is a prerequisite the protocol maintains operationally.

---

### Recommendation

1. **Introduce a same-block deposit lock**: Record the block number at deposit time in rsETH or the deposit pool, and prevent `instantWithdrawal` from being called in the same block as a deposit by the same address.
2. **Use a time-weighted or commit-reveal price update**: Delay the effect of `updateRSETHPrice()` by one block, or restrict it to a role that uses a private mempool (analogous to the original report's recommendation).
3. **Alternatively, gate `updateRSETHPrice()` to a privileged role** and submit price-update transactions via a private relay (e.g., Flashbots Protect) to prevent mempool visibility.

---

### Proof of Concept

```
Block N (front-run):
  Attacker calls LRTDepositPool.depositETH{value: 1000 ETH}()
  rsETHPrice = 1.05 ETH/rsETH  (stale, pre-reward)
  Attacker receives: 1000 / 1.05 ≈ 952.38 rsETH

Block N (target tx):
  updateRSETHPrice() executes
  totalETHInProtocol increased by 10 ETH of EigenLayer rewards
  rsETHPrice updated to 1.06 ETH/rsETH

Block N (back-run):
  Attacker calls LRTWithdrawalManager.instantWithdrawal(ETH, 952.38 rsETH)
  assetAmountUnlocked = 952.38 * 1.06 ≈ 1009.52 ETH
  (minus instantWithdrawalFee, e.g. 0.1% → fee = 1.009 ETH)
  Attacker receives: ≈ 1008.51 ETH

Net profit: ≈ 8.51 ETH stolen from legitimate rsETH holders
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

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

**File:** contracts/LRTWithdrawalManager.sol (L55-56)
```text
    mapping(address asset => bool) public isInstantWithdrawalEnabled;
    uint256 public instantWithdrawalFee; // Fee in basis points (1 = 0.01%)
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
