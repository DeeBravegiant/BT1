### Title
Stale `rsETHPrice` Used in Mint Calculation Without Prior Oracle Update — (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.depositETH()` and `depositAsset()` compute the rsETH mint amount using `LRTOracle.rsETHPrice`, a stored state variable that is only updated when `updateRSETHPrice()` is explicitly called. Neither deposit function triggers a price update before minting. When staking rewards have accrued since the last oracle update, the stored price is stale (below the true current price), causing depositors to receive more rsETH than the current fair value warrants, at the expense of existing holders' accrued yield.

---

### Finding Description

`LRTOracle.rsETHPrice` is a plain storage variable:

```solidity
// contracts/LRTOracle.sol:28
uint256 public override rsETHPrice;
```

It is only written inside `_updateRsETHPrice()`, which is invoked by the two public entry points `updateRSETHPrice()` and `updateRSETHPriceAsManager()`. A `grep` across all production Solidity files confirms `updateRSETHPrice` appears exclusively inside `LRTOracle.sol` — it is never called from `LRTDepositPool` or any other contract in the deposit path.

The mint-amount calculation in `LRTDepositPool` reads this stored value directly:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

This line is reached through the following call chain on every user deposit:

- `depositETH()` / `depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.rsETHPrice()`

None of these steps call `updateRSETHPrice()` first. As EigenLayer staking rewards and LST rebases accrue, the true TVL grows while `rsETHPrice` remains frozen at its last stored value. Any depositor who transacts during this staleness window receives:

```
rsethAmountToMint = depositValue / stalePrice   >   depositValue / truePrice
```

The excess rsETH minted to the new depositor represents a direct transfer of value away from existing holders, whose proportional claim on the protocol TVL is diluted.

Additionally, the `pricePercentageLimit` guard inside `_updateRsETHPrice()` will revert calls from non-manager accounts when the accumulated price increase exceeds the configured threshold:

```solidity
// contracts/LRTOracle.sol:260-265
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
}
```

This means that during periods of high reward accrual, the public `updateRSETHPrice()` call reverts for ordinary users, extending the staleness window and amplifying the exploitable gap.

---

### Impact Explanation

Every deposit made while `rsETHPrice` is stale mints excess rsETH. The excess is funded by diluting the share of all existing rsETH holders. The accrued staking yield that should belong to existing holders is instead partially captured by the new depositor. This constitutes **theft of unclaimed yield** from existing rsETH holders.

Impact: **High** — Theft of unclaimed yield.

---

### Likelihood Explanation

`rsETHPrice` is updated off-chain by a bot or keeper. There is always a non-zero window between consecutive updates (at minimum one block, in practice minutes to hours). The `pricePercentageLimit` mechanism can extend this window further by blocking public updates. Any depositor — including a sophisticated actor who monitors the mempool for pending oracle updates and front-runs them — can exploit this gap. No special role or privileged access is required; the entry point is the public `depositETH()` / `depositAsset()` functions.

Likelihood: **Medium** — Continuously present but requires timing awareness to maximize gain.

---

### Recommendation

Call `updateRSETHPrice()` (or refactor to an internal `_updateRsETHPrice()` call) at the start of `depositETH()` and `depositAsset()` before the mint amount is computed, mirroring the pattern recommended in the reference report:

```solidity
function depositETH(...) external payable nonReentrant whenNotPaused ... {
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // add this
    uint256 rsethAmountToMint = _beforeDeposit(...);
    _mintRsETH(rsethAmountToMint);
}
```

The same fix applies to `depositAsset()`. If the `pricePercentageLimit` guard is a concern for the deposit path, a dedicated internal update function that bypasses the threshold revert (or uses the manager path) should be used.

---

### Proof of Concept

1. At time T₀, `updateRSETHPrice()` is called; `rsETHPrice` is stored as `1.001e18` (1.001 ETH per rsETH).
2. Staking rewards accrue over the next N blocks; the true TVL-per-rsETH rises to `1.002e18`.
3. At time T₁ (before the next oracle update), Alice calls `depositETH{value: 1 ether}(0, "")`.
4. `getRsETHAmountToMint(ETH, 1e18)` computes: `1e18 * 1e18 / 1.001e18 ≈ 0.999001 rsETH` — but the fair amount at the true price would be `1e18 / 1.002e18 ≈ 0.998004 rsETH`.
5. Alice receives ~0.000997 rsETH more than she is entitled to. Multiplied across many depositors and many staleness windows, existing holders' accrued yield is continuously siphoned.
6. If `pricePercentageLimit` is set and the accumulated increase exceeds it, public calls to `updateRSETHPrice()` revert, keeping the price stale for longer and increasing the per-deposit excess.

**Root cause:** [1](#0-0)  reads the stored `rsETHPrice` [2](#0-1)  without first invoking `updateRSETHPrice()` [3](#0-2) , and neither `depositETH()` [4](#0-3)  nor `depositAsset()` [5](#0-4)  triggers the update before minting.

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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
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
